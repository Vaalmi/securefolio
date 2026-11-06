"""Each row of the job, revision and document tables, including each refusal."""

import time

import pytest

from backend import database, documents, jobs, refusals, revisions, states
from backend.database import document_locations


@pytest.fixture(autouse=True)
def ready(library):
    pass


def queued_document(tmp_path, name="a.txt") -> tuple[str, str]:
    path = tmp_path / name
    path.write_text("hello there")
    result = documents.import_paths([str(path)], states.STANDARD)[0]
    return result["document_id"], result["job_id"]


class TestJobs:
    def test_a_live_key_returns_the_same_job(self):
        first = jobs.enqueue(jobs.SCAN, 1, key="k")
        assert jobs.enqueue(jobs.SCAN, 1, key="k") == first

    def test_claim_takes_the_most_urgent_with_a_lease(self):
        jobs.enqueue(jobs.ORGANIZE, jobs.PRIORITY_ORGANIZE)
        urgent = jobs.enqueue(jobs.INGEST, jobs.PRIORITY_DROP)
        job = jobs.claim()
        assert job is not None
        assert job["id"] == urgent
        assert jobs.get(urgent)["state"] == states.RUNNING
        assert jobs.get(urgent)["lease_expires_at"] > time.time()

    def test_a_paused_folders_jobs_wait(self):
        with database.write() as connection:
            connection.execute(
                database.sources.update()
                .where(database.sources.c.id == states.LIBRARY_SOURCE_ID)
                .values(status=states.PAUSED)
            )
        jobs.enqueue(jobs.INGEST, 1, source_id=states.LIBRARY_SOURCE_ID)
        assert jobs.claim() is None

    def test_a_heartbeat_extends_only_a_running_job(self):
        job_id = jobs.enqueue(jobs.SCAN, 1)
        assert not jobs.heartbeat(job_id)
        jobs.claim()
        assert jobs.heartbeat(job_id)

    def test_an_expired_lease_requeues_then_fails_on_the_third_attempt(self):
        job_id = jobs.enqueue(jobs.SCAN, 1)
        for attempt in range(1, jobs.MAX_ATTEMPTS + 1):
            jobs.claim()
            moved = jobs.requeue_expired(time.time() + jobs.LEASE_SECONDS + 1)
            assert moved[0]["attempts"] == attempt
        assert jobs.get(job_id)["state"] == states.FAILED
        assert jobs.get(job_id)["reason"]

    def test_a_live_lease_isnt_requeued(self):
        jobs.enqueue(jobs.SCAN, 1)
        jobs.claim()
        assert jobs.requeue_expired() == []

    def test_cancel_stops_a_queued_job_now_and_a_running_one_at_its_next_stage(self):
        queued = jobs.enqueue(jobs.SCAN, 1)
        with database.write() as connection:
            assert jobs.request_cancel_in(connection, queued) == states.CANCELLED
        running = jobs.enqueue(jobs.SCAN, 1)
        jobs.claim()
        with database.write() as connection:
            assert jobs.request_cancel_in(connection, running) == states.RUNNING
        assert not jobs.advance_stage(running, "next")
        jobs.finish_cancelled(running)
        with database.write() as connection, pytest.raises(refusals.Refused) as refused:
            jobs.request_cancel_in(connection, running)
        assert refused.value.status == 409

    def test_finishing_needs_a_running_job(self):
        job_id = jobs.enqueue(jobs.SCAN, 1)
        with pytest.raises(refusals.Refused):
            jobs.succeed(job_id)
        jobs.claim()
        jobs.fail(job_id, "No.", "code")
        assert jobs.get(job_id)["reason"] == "No."

    def test_only_a_failed_or_cancelled_job_is_retried(self):
        job_id = jobs.enqueue(jobs.SCAN, 1)
        with database.write() as connection, pytest.raises(refusals.Refused):
            jobs.retry_in(connection, job_id)
        jobs.claim()
        jobs.fail(job_id, "No.", "code")
        with database.write() as connection:
            jobs.retry_in(connection, job_id)
        job = jobs.get(job_id)
        assert (job["state"], job["attempts"], job["reason"]) == (
            states.QUEUED,
            0,
            None,
        )

    def test_a_job_keeps_a_timeline(self):
        job_id = jobs.enqueue(jobs.SCAN, 1)
        jobs.claim()
        jobs.advance_stage(job_id, "parse")
        jobs.succeed(job_id)
        assert jobs.timeline(job_id) == [
            "queued",
            "running",
            "stage parse",
            states.SUCCEEDED,
        ]

    def test_an_unknown_job_is_a_404(self):
        with pytest.raises(refusals.Refused) as refused:
            jobs.get("nope")
        assert refused.value.status == 404


class TestRevisions:
    def stage(self, tmp_path) -> tuple[str, str, str]:
        document_id, job_id = queued_document(tmp_path)
        sha = documents.get(document_id)["content_sha256"]
        return document_id, job_id, revisions.stage(document_id, job_id, sha)

    def test_staging_again_for_the_same_job_returns_it(self, tmp_path):
        document_id, job_id, revision_id = self.stage(tmp_path)
        sha = documents.get(document_id)["content_sha256"]
        assert revisions.stage(document_id, job_id, sha) == revision_id

    def test_publish_switches_active_and_retires_the_old(self, tmp_path):
        document_id, job_id, first = self.stage(tmp_path)
        revisions.publish(first, job_id, make_indexed=True)
        assert documents.get(document_id)["active_revision_id"] == first
        assert documents.get(document_id)["status"] == states.INDEXED
        second = revisions.stage(
            document_id, "another", documents.get(document_id)["content_sha256"]
        )
        jobs_id = jobs.enqueue(jobs.INGEST, 1)
        revisions.publish(second, jobs_id, make_indexed=False)
        with database.read() as connection:
            state = (
                connection.execute(
                    database.revisions.select().where(database.revisions.c.id == first)
                )
                .mappings()
                .one()["state"]
            )
        assert state == states.RETIRED

    def test_publish_is_refused_when_not_staged_cancelled_removed_or_changed(
        self, tmp_path
    ):
        document_id, job_id, revision_id = self.stage(tmp_path)
        with pytest.raises(refusals.Refused):
            revisions.publish("nope", job_id, True)
        with database.write() as connection:
            documents.set_content_hash_in(connection, document_id, "different")
        with pytest.raises(refusals.Refused) as changed:
            revisions.publish(revision_id, job_id, True)
        assert "changed" in changed.value.detail
        with database.write() as connection:
            connection.execute(
                database.jobs.update()
                .where(database.jobs.c.id == job_id)
                .values(cancel_requested=True)
            )
        with pytest.raises(refusals.Refused) as cancelled:
            revisions.publish(revision_id, job_id, True)
        assert "cancelled" in cancelled.value.detail

    def test_discard_removes_a_staged_revision_only(self, tmp_path):
        document_id, job_id, revision_id = self.stage(tmp_path)
        revisions.write_content(
            revision_id,
            document_id,
            [
                {
                    "id": "e",
                    "ordinal": 0,
                    "kind": "text",
                    "text": "t",
                    "text_hash": "h",
                    "locator": {},
                }
            ],
            [
                {
                    "id": "c",
                    "ordinal": 0,
                    "element_ids": ["e"],
                    "text": "t",
                    "token_count": 1,
                }
            ],
        )
        revisions.discard_for_job(job_id)
        with database.read() as connection:
            for table in (database.elements, database.chunks, database.revisions):
                assert connection.execute(table.select()).first() is None


class TestDocumentMoves:
    def test_cancel_on_a_first_index_leaves_nothing_half_indexed(self, tmp_path):
        document_id, job_id = queued_document(tmp_path)
        documents.cancel_job(job_id)
        assert documents.get(document_id)["status"] == states.CANCELLED

    def test_retry_requeues_the_document_with_its_job(self, tmp_path):
        document_id, job_id = queued_document(tmp_path)
        documents.cancel_job(job_id)
        documents.retry_job(job_id)
        assert documents.get(document_id)["status"] == states.QUEUED

    def test_unknown_jobs_and_documents_are_404s(self):
        for call in (
            lambda: documents.cancel_job("x"),
            lambda: documents.retry_job("x"),
            lambda: documents.get("x"),
        ):
            with pytest.raises(refusals.Refused) as refused:
                call()
            assert refused.value.status == 404

    def test_unlock_needs_a_document_waiting_for_a_password(self, tmp_path):
        document_id, _ = queued_document(tmp_path)
        with pytest.raises(refusals.Refused):
            documents.unlock_and_index(document_id)

    def test_download_is_refused_when_offline_or_already_here(self, tmp_path):
        from backend import settings  # noqa: PLC0415

        document_id, _ = queued_document(tmp_path)
        with pytest.raises(refusals.Refused) as here:
            documents.download_and_index(document_id)
        assert "already" in here.value.detail
        settings.put(settings.OFFLINE, True)
        with pytest.raises(refusals.Refused) as offline:
            documents.download_and_index(document_id)
        assert "Offline" in offline.value.detail

    def test_reindex_needs_an_indexed_document(self, tmp_path):
        document_id, _ = queued_document(tmp_path)
        with pytest.raises(refusals.Refused):
            documents.reindex(document_id)

    def test_relocate_needs_a_missing_document_and_a_real_file(self, tmp_path):
        document_id, _ = queued_document(tmp_path)
        with pytest.raises(refusals.Refused) as nothing:
            documents.relocate(document_id, str(tmp_path / "gone"), False)
        assert nothing.value.status == 422
        with pytest.raises(refusals.Refused) as not_missing:
            documents.relocate(document_id, str(tmp_path / "a.txt"), False)
        assert not_missing.value.status == 409

    def test_trash_needs_a_managed_copy(self, tmp_path):
        document_id, _ = queued_document(tmp_path)
        with database.write() as connection:
            connection.execute(
                document_locations.update().values(storage_mode=states.REFERENCED)
            )
        with pytest.raises(refusals.Refused) as refused:
            documents.trash_managed_copy(document_id)
        assert refused.value.status == 409
