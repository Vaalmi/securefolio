"""A staged revision is never searchable; publish and the index stay in step."""

import pytest
import sqlalchemy as sa

from backend import (
    database,
    documents,
    embedder,
    index,
    jobs,
    model_budget,
    reranker,
    revisions,
    settings,
    sources,
    states,
)
from testing.counts import Counts
from testing.queue import drain, next_job
from worker import embedding, housekeeping, parse, pipeline, scan


@pytest.fixture
def folder(library, tmp_path):
    path = tmp_path / "docs"
    path.mkdir()
    (path / "a.txt").write_text("The quartz clause says rent is due monthly.")
    return path


def find(text: str) -> list:
    return index.search(text, None, None, 5)


def test_nothing_is_searchable_until_publish(folder, monkeypatch):
    seen_during_staging = []
    real = embedding.embed_revision

    def checking(revision_id):
        real(revision_id)
        seen_during_staging.append(find("quartz"))

    monkeypatch.setattr(embedding, "embed_revision", checking)
    sources.add_source(str(folder), states.STANDARD)
    drain()
    assert seen_during_staging == [[]]
    assert find("quartz")


def test_a_crash_between_publish_and_the_index_write_is_replayed(folder, monkeypatch):
    sources.add_source(str(folder), states.STANDARD)
    with monkeypatch.context() as patched:
        patched.setattr(index, "replay_ops", lambda: 0)
        drain()
    assert find("quartz") == []
    with database.read() as connection:
        assert connection.execute(sa.select(database.index_ops)).first() is not None
    index.sweep()
    assert find("quartz")
    with database.read() as connection:
        assert connection.execute(sa.select(database.index_ops)).first() is None


def test_the_sweep_removes_rows_whose_revision_isnt_active(folder):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.write() as connection:
        connection.execute(database.documents.update().values(active_revision_id=None))
    index.sweep()
    assert find("quartz") == []


def test_the_index_rebuilds_with_no_parse_or_embed(folder, monkeypatch):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    counts = Counts()
    monkeypatch.setattr(parse, "parse", counts.wrap_parse(parse.parse))
    monkeypatch.setattr(
        embedder, "embed_documents", counts.wrap_embed(embedder.embed_documents)
    )
    index.rebuild()
    assert find("quartz")
    assert (counts.parses, counts.embeds) == (0, 0)


def test_a_new_generation_builds_beside_the_old_and_swaps_only_when_told(folder):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    old = index.current_generation()
    with database.write() as connection:
        new = connection.execute(
            database.index_generations.insert().values(
                state="building", **embedder.describe()
            )
        ).lastrowid
    with database.read() as connection:
        doc = connection.execute(sa.select(database.documents)).mappings().one()
    index.publish(doc["id"], doc["active_revision_id"], new)
    assert index.current_generation() == old
    assert find("quartz")
    index.rebuild(new)
    assert index._table(new).count_rows() == 1
    assert index._table(old).count_rows() == 1
    with database.read() as connection:
        recorded = (
            connection.execute(
                sa.select(database.index_generations).where(
                    database.index_generations.c.id == new
                )
            )
            .mappings()
            .one()
        )
    assert recorded["dimension"] == embedder.DIMENSION
    assert recorded["metric"] == "cosine"
    assert recorded["truncation"] == embedder.TRUNCATION


def test_optimize_runs_and_a_prefilter_applies_before_scoring(folder):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    housekeeping.run_optimize({})
    assert index.search("quartz", None, index.where_clause(doc_type="Nope"), 5) == []
    assert index.search("quartz", embedder.embed_query("quartz"), None, 5)
    assert index.count() >= 1


def test_a_query_of_only_punctuation_finds_nothing(folder):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    assert index.search("!!!", None, None, 5) == []


def test_chunks_store_float16_vectors(folder):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        blob = connection.execute(sa.select(database.chunks.c.embedding)).scalar_one()
    assert len(blob) == embedder.DIMENSION * 2


def test_cancel_a_running_job_between_stages_leaves_no_orphans(folder, monkeypatch):
    sources.add_source(str(folder), states.STANDARD)
    scan_job = next_job()
    scan.run(scan_job)
    jobs.succeed(scan_job["id"])
    job = next_job()
    real = embedding.embed_revision

    def then_cancel(revision_id):
        real(revision_id)
        documents.cancel_job(job["id"])

    monkeypatch.setattr(embedding, "embed_revision", then_cancel)
    pipeline.run(job)
    assert jobs.get(job["id"])["state"] == states.CANCELLED
    assert documents.get(job["document_id"])["status"] == states.CANCELLED
    with database.read() as connection:
        for table in (database.elements, database.chunks, database.revisions):
            assert connection.execute(sa.select(table)).first() is None
    assert find("quartz") == []


def test_a_running_reindex_cancelled_leaves_the_active_revision_searchable(
    folder, monkeypatch
):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        document = connection.execute(sa.select(database.documents)).mappings().one()
    documents.reindex(document["id"])
    job = next_job()
    real = embedding.embed_revision

    def then_cancel(revision_id):
        real(revision_id)
        documents.cancel_job(job["id"])

    monkeypatch.setattr(embedding, "embed_revision", then_cancel)
    pipeline.run(job)
    after = documents.get(document["id"])
    assert after["status"] == states.INDEXED
    assert after["active_revision_id"] == document["active_revision_id"]
    assert find("quartz")


def test_a_reindex_failure_keeps_the_document_indexed(folder, monkeypatch):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        document = connection.execute(sa.select(database.documents)).mappings().one()
    documents.reindex(document["id"])

    def broken(*_args):
        raise parse.CantRead("It broke.")

    monkeypatch.setattr(parse, "parse", broken)
    drain()
    assert documents.get(document["id"])["status"] == states.INDEXED
    assert find("quartz")


def test_the_worker_gives_up_its_embedder_while_the_reranker_is_loaded(
    folder, monkeypatch
):
    unloaded = []
    calls = iter([True, False])
    monkeypatch.setattr(model_budget, "reranker_loaded", lambda: next(calls))
    monkeypatch.setattr(embedder, "unload", lambda: unloaded.append(1))
    monkeypatch.setattr(embedding, "WAIT_SECONDS", 0)
    embedding.wait_for_room()
    assert unloaded == [1]


def test_a_failed_reranker_load_holds_nothing_back(library, monkeypatch):
    def fails():
        raise OSError("no model")

    monkeypatch.setattr(reranker.build_mode, "uses_stub_models", lambda: False)
    monkeypatch.setattr(reranker, "_load", fails)
    with pytest.raises(OSError, match="no model"):
        reranker.score("a question", ["a passage"])
    assert not model_budget.reranker_loaded()


def test_a_budget_flag_from_a_dead_process_holds_nothing_back(library):
    settings.put(model_budget.RERANKER_LOADED, 2**30)  # no such process
    assert not model_budget.reranker_loaded()


def test_the_budget_flag_round_trips(library):
    assert not model_budget.reranker_loaded()
    model_budget.set_reranker_loaded(True)
    assert model_budget.reranker_loaded()


def test_idle_models_unload(monkeypatch):
    monkeypatch.setattr(embedder._state, "model", object())
    monkeypatch.setattr(embedder._state, "used_at", 0.0)
    assert not embedder.unload_if_idle(embedder.IDLE_SECONDS - 1)
    monkeypatch.setattr(embedder, "clear_metal_cache", lambda: None)
    assert embedder.unload_if_idle(embedder.IDLE_SECONDS + 1)
    assert embedder._state.model is None


def test_retired_revisions_keep_elements_only_when_cited(folder):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        document = connection.execute(sa.select(database.documents)).mappings().one()
    cited = document["active_revision_id"]
    with database.write() as connection:
        connection.execute(
            database.notes.insert().values(
                id="n",
                title="n",
                body="b",
                citations=[{"document_id": document["id"], "revision_id": cited}],
                source_label="x",
                sensitivity=states.STANDARD,
                created_at=1,
            )
        )
    documents.reindex(document["id"])
    drain()
    with database.read() as connection:
        kept = connection.execute(
            sa.select(database.elements).where(database.elements.c.revision_id == cited)
        ).first()
        chunks = connection.execute(
            sa.select(database.chunks).where(database.chunks.c.revision_id == cited)
        ).first()
    assert kept is not None
    assert chunks is None


def test_revisions_expose_active_elements(folder):
    sources.add_source(str(folder), states.STANDARD)
    drain()
    with database.read() as connection:
        document_id = connection.execute(
            sa.select(database.documents.c.id)
        ).scalar_one()
    assert revisions.active_elements(document_id)[0]["text"].startswith("The quartz")
