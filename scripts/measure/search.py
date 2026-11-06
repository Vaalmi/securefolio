"""Typeahead, list filters and sort at full size: a 20,000-document, 600,000-chunk
synthetic vault in a temporary folder, through the real modules, with the real query
embedder for the warm path. Its stored vectors are random.
Run: PYTHONPATH=. HF_HUB_OFFLINE=1 HF_HOME=$PWD/.cache/models/huggingface \
  uv run python scripts/measure/search.py"""

import json
import os
import random
import statistics
import tempfile
import time
import uuid

import numpy as np
import pyarrow as pa
import sqlalchemy as sa

from backend import telemetry

telemetry.turn_off()  # before any model loads

DOCUMENTS = 20_000
CHUNKS_EACH = 30
SEARCHES = 200
LIST_READS = 100
WORDS = [f"word{i}" for i in range(20_000)]
TYPES = ["Contract", "Receipt", "Statement", "Report", "Letter", "Policy", "Other"]
BATCH_DOCUMENTS = 2000
FAKE_HASH = "0" * 64


def p95(samples: list[float]) -> float:
    return round(statistics.quantiles(samples, n=20)[18] * 1000, 1)


def build(rng: np.random.Generator, py: random.Random) -> tuple[list[str], str]:
    """Rows straight into SQLite and LanceDB, which is what a publish leaves behind."""
    from backend import database, index, states  # noqa: PLC0415
    from backend.embedder import DIMENSION  # noqa: PLC0415

    table = index._table()  # noqa: SLF001
    source_id = states.LIBRARY_SOURCE_ID
    all_titles = []
    locator = json.dumps({"kind": "pdf", "page": 1, "rects": [], "precision": "page"})
    for start in range(0, DOCUMENTS, BATCH_DOCUMENTS):
        documents, locations, revisions, fts = [], [], [], []
        ids, doc_ids, rev_ids, levels, types, dates, texts = [], [], [], [], [], [], []
        elements, chunks = [], []
        for number in range(start, start + BATCH_DOCUMENTS):
            doc_id, rev_id = uuid.uuid4().hex, uuid.uuid4().hex
            title = f"Document {number} {py.choice(WORDS)}"
            all_titles.append(title)
            level = states.PRIVATE if number % 10 == 0 else states.STANDARD
            kind = py.choice(TYPES)
            date = f"{py.randint(2015, 2026)}-{py.randint(1, 12):02d}-01"
            documents.append(
                {
                    "id": doc_id,
                    "title": title,
                    "file_name": f"{title}.pdf",
                    "file_kind": "PDF",
                    "content_sha256": uuid.uuid4().hex,
                    "active_revision_id": rev_id,
                    "status": states.INDEXED,
                    "sensitivity": level,
                    "starred": number % 50 == 0,
                    "size_bytes": py.randint(1000, 9_000_000),
                    "pages": 5,
                    "doc_date": date,
                    "doc_type": kind,
                    "added_at": float(number),
                }
            )
            locations.append(
                {
                    "id": uuid.uuid4().hex,
                    "document_id": doc_id,
                    "source_id": source_id,
                    "path": f"/synthetic/{doc_id}.pdf",
                    "storage_mode": states.MANAGED,
                    "sensitivity": level,
                }
            )
            revisions.append(
                {
                    "id": rev_id,
                    "document_id": doc_id,
                    "content_sha256": FAKE_HASH,
                    "parser_version": "synthetic",
                    "state": states.ACTIVE,
                    "created_at": float(number),
                }
            )
            fts.append((doc_id, title, f"{title}.pdf", f"/synthetic/{doc_id}.pdf"))
            for chunk in range(CHUNKS_EACH):
                text = " ".join(py.choices(WORDS, k=60))
                chunk_id = f"{doc_id}-{chunk}"
                element_id = uuid.uuid4().hex
                ids.append(chunk_id)
                doc_ids.append(doc_id)
                rev_ids.append(rev_id)
                levels.append(level)
                types.append(kind)
                dates.append(date)
                texts.append(text)
                elements.append((element_id, rev_id, chunk, "text", text, "", locator))
                chunks.append(
                    (
                        chunk_id,
                        rev_id,
                        doc_id,
                        chunk,
                        json.dumps([element_id]),
                        "",
                        60,
                        level,
                    )
                )
        vectors = rng.standard_normal((len(ids), DIMENSION), dtype=np.float32)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        table.add(
            pa.table(
                {
                    "id": ids,
                    "document_id": doc_ids,
                    "revision_id": rev_ids,
                    "sensitivity": levels,
                    "doc_type": types,
                    "doc_date": dates,
                    "text": texts,
                    "vector": pa.FixedSizeListArray.from_arrays(
                        pa.array(vectors.ravel()), DIMENSION
                    ),
                }
            )
        )
        with database.write() as connection:
            connection.execute(database.documents.insert(), documents)
            connection.execute(database.document_locations.insert(), locations)
            connection.execute(database.revisions.insert(), revisions)
            connection.exec_driver_sql(
                "INSERT INTO documents_fts (document_id, title, file_name, path, "
                "summary, entities) VALUES (?, ?, ?, ?, '', '')",
                fts,
            )
            connection.exec_driver_sql(
                "INSERT INTO elements (id, revision_id, ordinal, kind, text, "
                "text_hash, locator, attempts) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
                elements,
            )
            connection.exec_driver_sql(
                "INSERT INTO chunks (id, revision_id, document_id, ordinal, "
                "element_ids, text, token_count, sensitivity) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                chunks,
            )
        print(f"built {start + BATCH_DOCUMENTS}", flush=True)
    table.create_index("text", config=index.FTS(), replace=True)
    index.optimize()
    with database.read() as connection:
        total = connection.execute(
            sa.select(sa.func.count()).select_from(database.chunks)
        )
        return all_titles, f"chunks {total.scalar_one()}"


def measure_search(py: random.Random, titles: list[str], vector: bool) -> float:
    from backend import search  # noqa: PLC0415

    samples = []
    for _ in range(SEARCHES):
        shape = py.randrange(3)
        if shape == 0:
            query = py.choice(titles)
        elif shape == 1:
            query = py.choice(titles)[:6]
        else:
            query = " ".join(py.choices(WORDS, k=2))
        begun = time.perf_counter()
        search.run(query, search.Scope(), ready=lambda: vector)
        samples.append(time.perf_counter() - begun)
    return p95(samples)


def measure_lists(py: random.Random) -> dict[str, float]:
    from backend import library  # noqa: PLC0415

    found: dict[str, float] = {}
    cases = {
        "default_sort": lambda: library.window(library.Filters(), "date", True, 0, 200),
        "title_sort": lambda: library.window(library.Filters(), "title", False, 0, 200),
        "size_sort_deep": lambda: library.window(
            library.Filters(), "size", True, py.randrange(19_000), 200
        ),
        "type_filter": lambda: library.window(
            library.Filters(doc_type=py.choice(TYPES)), "date", True, 0, 200
        ),
        "date_filter": lambda: library.window(
            library.Filters(date_from="2024-01-01"), "title", False, 0, 200
        ),
        "name_filter": lambda: library.window(
            library.Filters(name=f"word{py.randrange(20_000)}"), "date", True, 0, 200
        ),
        "facets": lambda: library.facets(library.Filters()),
    }
    for name, read in cases.items():
        samples = []
        for _ in range(LIST_READS):
            begun = time.perf_counter()
            read()
            samples.append(time.perf_counter() - begun)
        found[name] = p95(samples)
    return found


def measure_tray() -> float:
    """The tray's counts with a finished ingest job for every document, which the
    open stream reads each second."""
    from backend import database, jobs, states, tray  # noqa: PLC0415

    with database.read() as connection:
        ids = connection.execute(sa.select(database.documents.c.id)).scalars().all()
    rows = [
        {
            "id": uuid.uuid4().hex,
            "kind": jobs.INGEST,
            "document_id": document_id,
            "priority": jobs.PRIORITY_BULK,
            "state": states.SUCCEEDED,
            "payload": {},
            "created_at": float(number),
            "updated_at": float(number),
        }
        for number, document_id in enumerate(ids)
    ]
    with database.write() as connection:
        connection.execute(database.jobs.insert(), rows)
    samples = []
    for _ in range(LIST_READS):
        begun = time.perf_counter()
        tray.counts()
        samples.append(time.perf_counter() - begun)
    return p95(samples)


def main() -> None:
    with tempfile.TemporaryDirectory() as root:
        os.environ["SECUREFOLIO_VAULT"] = f"{root}/vault"
        os.environ["SECUREFOLIO_STUB_MODELS"] = "1"
        from backend import embedder, sources  # noqa: PLC0415
        from backend.migrations import run as migrations  # noqa: PLC0415

        migrations.upgrade()
        sources.ensure_library_source()
        rng = np.random.default_rng(7)
        py = random.Random(7)  # noqa: S311
        begun = time.time()
        titles, size = build(rng, py)
        print(f"{size}, built in {time.time() - begun:.0f} s", flush=True)
        results = {
            "typeahead_bm25_p95_ms": measure_search(py, titles, False),
            "lists_p95_ms": measure_lists(py),
            "tray_counts_p95_ms": measure_tray(),
        }
        del os.environ["SECUREFOLIO_STUB_MODELS"]
        embedder.start_loading()
        while not embedder.is_ready():
            time.sleep(0.2)
        embedder.embed_query("warm up")
        results["typeahead_warm_real_embedder_p95_ms"] = measure_search(
            py, titles, True
        )
        print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
