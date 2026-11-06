"""Embeds a staged revision and stores the vectors with its chunks. While an Ask's
reranker is loaded in the backend, this holds back and frees its own model."""

import time

from backend import embedder, model_budget, revisions

BATCH = 64
WAIT_SECONDS = 1.0


def wait_for_room() -> None:
    while model_budget.reranker_loaded():
        embedder.unload()
        time.sleep(WAIT_SECONDS)


def embed_revision(revision_id: str) -> None:
    """Idempotent: only chunks with no vector yet are embedded."""
    pending = revisions.unembedded(revision_id)
    for start in range(0, len(pending), BATCH):
        wait_for_room()
        batch = pending[start : start + BATCH]
        vectors = embedder.embed_documents([text for _, text in batch])
        blobs = [embedder.to_blob(vector) for vector in vectors]
        revisions.store_embeddings(
            [(chunk, blob) for (chunk, _), blob in zip(batch, blobs, strict=True)]
        )
