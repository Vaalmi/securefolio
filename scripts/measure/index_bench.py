"""LanceDB against Chroma plus FTS5, on the same synthetic chunks: hybrid and
filtered search times, and two processes writing LanceDB at once.
Run: uv run --with chromadb python scripts/measure/index_bench.py <chunks>"""

import importlib
import json
import multiprocessing
import random
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from pathlib import Path

import lancedb
import numpy as np
import pyarrow as pa
from lancedb.rerankers import RRFReranker

WORDS = [f"word{i}" for i in range(20000)]
DIMENSION = 1024
QUERIES = 100
TOP = 50


def random_vectors(rng: np.random.Generator, count: int) -> np.ndarray:
    vectors = rng.standard_normal((count, DIMENSION), dtype=np.float32)
    return vectors / np.linalg.norm(vectors, axis=1, keepdims=True)


def batches(total: int, size: int = 50000):
    rng = np.random.default_rng(7)
    py = random.Random(7)  # noqa: S311
    for start in range(0, total, size):
        count = min(size, total - start)
        vectors = random_vectors(rng, count)
        text = [" ".join(py.choices(WORDS, k=60)) for _ in range(count)]
        ids = list(range(start, start + count))
        yield ids, text, vectors


def p95(samples: list[float]) -> float:
    return round(statistics.quantiles(samples, n=20)[18] * 1000, 1)


def bench_lance(total: int, root: Path) -> dict:
    db = lancedb.connect(root / "lance")
    table = None
    for ids, text, vectors in batches(total):
        data = pa.table(
            {
                "id": ids,
                "text": text,
                "doc": [i // 30 for i in ids],
                "sensitivity": ["standard" if i % 10 else "private" for i in ids],
                "vector": pa.FixedSizeListArray.from_arrays(
                    pa.array(vectors.ravel()), DIMENSION
                ),
            }
        )
        if table is None:
            table = db.create_table("chunks", data)
        else:
            table.add(data)
    built = time.time()
    table.create_fts_index("text")
    table.create_index(
        metric="cosine",
        num_partitions=256,
        num_sub_vectors=32,
        vector_column_name="vector",
    )
    table.create_scalar_index("doc")
    table.create_scalar_index("sensitivity", index_type="BITMAP")
    index_seconds = time.time() - built
    rng = np.random.default_rng(1)
    query_vectors = random_vectors(rng, QUERIES)
    reranker = RRFReranker()
    hybrid, filtered = [], []
    for i in range(QUERIES):
        words = " ".join(random.sample(WORDS, 3))
        begun = time.time()
        (
            table.search(query_type="hybrid")
            .vector(query_vectors[i])
            .text(words)
            .where("sensitivity = 'standard'", prefilter=True)
            .rerank(reranker)
            .limit(TOP)
            .to_list()
        )
        hybrid.append(time.time() - begun)
        docs = ",".join(str(int(i)) for i in rng.integers(total // 30, size=20))
        begun = time.time()
        (
            table.search(query_vectors[i])
            .where(f"doc IN ({docs})", prefilter=True)
            .limit(TOP)
            .to_list()
        )
        filtered.append(time.time() - begun)
    return {
        "hybrid_p95_ms": p95(hybrid),
        "filtered_p95_ms": p95(filtered),
        "index_seconds": round(index_seconds),
    }


def bench_chroma_fts(total: int, root: Path) -> dict:
    chromadb = importlib.import_module("chromadb")
    client = chromadb.PersistentClient(str(root / "chroma"))
    collection = client.create_collection("chunks", metadata={"hnsw:space": "cosine"})
    fts = sqlite3.connect(root / "fts.db")
    fts.execute("CREATE VIRTUAL TABLE chunks USING fts5(text, id UNINDEXED)")
    for ids, text, vectors in batches(total, 5000):
        collection.add(
            ids=[str(i) for i in ids],
            embeddings=vectors.tolist(),
            metadatas=[
                {"sensitivity": "standard" if i % 10 else "private"} for i in ids
            ],
        )
        fts.executemany("INSERT INTO chunks VALUES (?, ?)", zip(text, ids, strict=True))
    fts.commit()
    query_vectors = random_vectors(np.random.default_rng(1), QUERIES)
    hybrid, filtered = [], []
    rng = np.random.default_rng(1)
    for i in range(QUERIES):
        words = " OR ".join(random.sample(WORDS, 3))
        begun = time.time()
        found = collection.query(
            query_embeddings=[query_vectors[i].tolist()],
            n_results=TOP,
            where={"sensitivity": "standard"},
        )
        rows = fts.execute(
            "SELECT id FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT 50",
            (words,),
        ).fetchall()
        ranks: dict[str, float] = {}
        for rank, key in enumerate(found["ids"][0]):
            ranks[key] = ranks.get(key, 0) + 1 / (60 + rank)
        for rank, (key,) in enumerate(rows):
            ranks[str(key)] = ranks.get(str(key), 0) + 1 / (60 + rank)
        hybrid.append(time.time() - begun)
        docs = [int(j) for j in rng.integers(total // 30, size=20)]
        begun = time.time()
        collection.query(
            query_embeddings=[query_vectors[i].tolist()],
            n_results=TOP,
            where={"doc": {"$in": docs}},
        )
        filtered.append(time.time() - begun)
    return {"hybrid_p95_ms": p95(hybrid), "filtered_p95_ms": p95(filtered)}


def _writer(path: str, base: int, rounds: int) -> None:
    table = lancedb.connect(path).open_table("w")
    for r in range(rounds):
        for attempt in range(20):
            try:
                table.add([{"id": base + r, "v": 1}])
                break
            except Exception:
                time.sleep(0.05 * (attempt + 1))
        if base == 0 and r % 5 == 0:
            for attempt in range(20):
                try:
                    table.update(where=f"id = {r}", values={"v": 2})
                    break
                except Exception:
                    time.sleep(0.05 * (attempt + 1))


def two_writers(root: Path, rounds: int = 200) -> dict:
    path = str(root / "w")
    db = lancedb.connect(path)
    db.create_table("w", [{"id": -1, "v": 0}])
    processes = [
        multiprocessing.Process(target=_writer, args=(path, base, rounds))
        for base in (0, 100000)
    ]
    for process in processes:
        process.start()
    for process in processes:
        process.join()
    rows = db.open_table("w").count_rows()
    return {"expected_rows": 2 * rounds + 1, "rows": rows}


def main() -> None:
    total = int(sys.argv[1])
    root = Path(tempfile.mkdtemp(prefix="securefolio-bench-"))
    try:
        print(json.dumps({"two_writers": two_writers(root)}), flush=True)
        print(
            json.dumps({"chunks": total, "lancedb": bench_lance(total, root)}),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "chunks": total,
                    "chroma_fts5": bench_chroma_fts(total, root),
                }
            ),
            flush=True,
        )
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    main()
