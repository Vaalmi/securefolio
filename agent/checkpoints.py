"""The graph's checkpoints in library.db: restoring a turn, pruning idle threads."""

import time
from typing import Any

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agent import api

IDLE_SECONDS = 7 * 24 * 3600
TABLES = ("checkpoints", "writes")


def saver() -> Any:
    """An async context manager holding the saver's connection."""
    return AsyncSqliteSaver.from_conn_string(api.checkpoint_path())


async def latest_id(checkpointer: Any, thread_id: str) -> str | None:
    found = await checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
    return found.config["configurable"]["checkpoint_id"] if found else None


async def restore(checkpointer: Any, thread_id: str, checkpoint_id: str | None) -> None:
    """Back to before the turn. Checkpoint ids sort by time."""
    async with checkpointer.lock:
        for table in TABLES:
            if checkpoint_id is None:
                await checkpointer.conn.execute(
                    f"DELETE FROM {table} WHERE thread_id = ?",  # noqa: S608
                    (thread_id,),
                )
            else:
                await checkpointer.conn.execute(
                    f"DELETE FROM {table} WHERE thread_id = ? "  # noqa: S608
                    "AND checkpoint_id > ?",
                    (thread_id, checkpoint_id),
                )
        await checkpointer.conn.commit()


async def delete(checkpointer: Any, thread_id: str) -> None:
    await restore(checkpointer, thread_id, None)


async def prune_idle(checkpointer: Any, now: float | None = None) -> int:
    """Threads idle 7 days keep only their latest checkpoint. The transcript stays."""
    cutoff = (time.time() if now is None else now) - IDLE_SECONDS
    idle = api.threads.idle_since(cutoff)
    for thread_id in idle:
        latest = await latest_id(checkpointer, thread_id)
        if latest is None:
            continue
        async with checkpointer.lock:
            for table in TABLES:
                await checkpointer.conn.execute(
                    f"DELETE FROM {table} WHERE thread_id = ? "  # noqa: S608
                    "AND checkpoint_id < ?",
                    (thread_id, latest),
                )
            await checkpointer.conn.commit()
    return len(idle)
