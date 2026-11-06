"""The backend's one way to the agent: runs, checkpoints, memory operations, and the
timers that keep them tidy."""

import asyncio
import hashlib
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from langgraph.types import Command

from agent import checkpoints, harness, memory, models
from backend import (
    backup,
    database,
    keychain,
    memories,
    model_store,
    policy,
    providers,
    reranker,
    settings,
    threads,
    tray,
)

logger = logging.getLogger(__name__)
FLUSH_SECONDS = 0.04
IDLE_FOR_MEMORY_SECONDS = 300
TICK_SECONDS = 60
RUN_FAILED = "This answer couldn't be finished."


_in_thread = asyncio.to_thread


class Service:
    """Holds the saver, the compiled graph and the runs in progress."""

    def __init__(self) -> None:
        self.tasks: dict[str, asyncio.Task[None]] = {}
        self.signals: dict[str, asyncio.Event] = {}
        self.context: Any = None
        self.saver: Any = None
        self.graph: Any = None
        self.timer: asyncio.Task[None] | None = None

    async def open(self) -> None:
        self.context = checkpoints.saver()
        self.saver = await self.context.__aenter__()
        await self.saver.setup()
        self.graph = harness.build(self.saver)
        await self.sweep()
        self.timer = asyncio.create_task(self._ticks())

    async def close(self) -> None:
        waiting = list(self.tasks.values())
        if self.timer:
            waiting.append(self.timer)
        for task in waiting:
            task.cancel()
        await asyncio.gather(*waiting, return_exceptions=True)
        if self.context:
            await self.context.__aexit__(None, None, None)

    async def sweep(self) -> None:
        """A run still running at startup died with its process."""
        for run in threads.dead_runs():
            await _in_thread(threads.stop_run, run["id"], threads.FAILED, RUN_FAILED)
            await checkpoints.restore(
                self.saver, run["thread_id"], run["checkpoint_id"]
            )

    async def _ticks(self) -> None:
        database.bind_to_this_library()
        while True:
            await asyncio.sleep(TICK_SECONDS)
            try:
                await self.tick()
            except Exception:
                logger.exception("the timer's work failed")

    async def tick(self, now: float | None = None) -> None:
        moment = time.time() if now is None else now
        for thread_id in threads.idle_for_memory(moment - IDLE_FOR_MEMORY_SECONDS):
            if threads.active_run(thread_id) is None:
                await self._extract(thread_id)
        await _in_thread(memories.expire_recaps, moment)
        await self._ask_those_waiting()
        await checkpoints.prune_idle(self.saver, moment)
        await _in_thread(backup.run_if_due, moment)
        reranker.unload_if_idle()

    async def _ask_those_waiting(self) -> None:
        """Once the queue drains, under the policy of that moment."""
        if tray.counts()["active"] > 0:
            return
        for waiting in threads.waiting_for_indexing():
            if threads.active_run(waiting["id"]) is None:
                await self.start(
                    waiting["id"], waiting["ask_when_indexed"], waiting["mode"]
                )

    async def _extract(self, thread_id: str) -> None:
        try:
            await memory.extract(thread_id)
        except (models.CloudRefused, models.NoConnection, models.LocalNotReady):
            logger.info("memory extraction waits")

    # ---- runs ----

    async def start(self, thread_id: str, question: str, mode: str) -> dict[str, Any]:
        """A new message cancels a pending card: its paused run is superseded."""
        active = threads.active_run(thread_id)
        if active and active["state"] == threads.PAUSED:
            await self._undo(active, threads.SUPERSEDED)
        checkpoint_id = await checkpoints.latest_id(self.saver, thread_id)
        run_id = await _in_thread(threads.start_run, thread_id, question, checkpoint_id)
        await _in_thread(threads.set_title_if_new, thread_id, question)
        given = {"question": question, "mode": mode}
        self._launch(thread_id, run_id, given)
        return threads.get_run(run_id)

    def _launch(self, thread_id: str, run_id: str, given: Any) -> None:
        """A task of its own, so a dropped connection lets the run finish."""
        self.signals[run_id] = asyncio.Event()
        self.tasks[run_id] = asyncio.create_task(self._drive(thread_id, run_id, given))

    def _add_event(self, run_id: str, event: str, data: dict[str, Any]) -> None:
        threads.add_event(run_id, event, data)
        signal = self.signals.get(run_id)
        if signal:
            signal.set()

    async def _drive(self, thread_id: str, run_id: str, given: Any) -> None:
        config = {"configurable": {"thread_id": thread_id, "run_id": run_id}}
        database.bind_to_this_library()
        try:
            await self._stream(run_id, given, config)
            await self._finish(thread_id, run_id, config)
        except asyncio.CancelledError:
            raise
        except (models.CloudRefused, models.LocalNotReady) as refused:
            await self._fail(thread_id, run_id, str(refused))
        except Exception:
            logger.exception("a run failed")
            await self._fail(thread_id, run_id, RUN_FAILED)
        finally:
            self.tasks.pop(run_id, None)

    async def _stream(self, run_id: str, given: Any, config: dict[str, Any]) -> None:
        pending: list[str] = []
        flushed = time.monotonic()
        async for mode, chunk in self.graph.astream(
            given, config, stream_mode=["updates", "custom"]
        ):
            if mode == "custom":
                if chunk["event"] == "token":
                    pending.append(chunk["data"]["text"])
                    if time.monotonic() - flushed < FLUSH_SECONDS:
                        continue
                    flushed = time.monotonic()
                    data = {"text": "".join(pending)}
                    pending.clear()
                    await asyncio.to_thread(self._add_event, run_id, "token", data)
                else:
                    await asyncio.to_thread(
                        self._add_event, run_id, chunk["event"], chunk["data"]
                    )
        if pending:
            data = {"text": "".join(pending)}
            await asyncio.to_thread(self._add_event, run_id, "token", data)

    async def _finish(
        self, thread_id: str, run_id: str, config: dict[str, Any]
    ) -> None:
        snapshot = await self.graph.aget_state(config)
        waiting = [i.value for task in snapshot.tasks for i in task.interrupts]
        if waiting:
            await _in_thread(threads.pause_run, run_id, waiting[0])
            await _in_thread(self._add_event, run_id, "card", waiting[0])
            return
        await _in_thread(threads.complete_run, run_id)
        done = {"state": threads.COMPLETED}
        await _in_thread(self._add_event, run_id, "done", done)

    async def _fail(self, thread_id: str, run_id: str, reason: str) -> None:
        run = threads.get_run(run_id)
        if run["state"] not in threads.ACTIVE:
            return
        await _in_thread(threads.stop_run, run_id, threads.FAILED, reason)
        await checkpoints.restore(self.saver, thread_id, run["checkpoint_id"])
        done = {"state": threads.FAILED, "error": reason}
        await _in_thread(self._add_event, run_id, "done", done)

    async def resume(self, thread_id: str, card_id: str, choice: str) -> dict[str, Any]:
        run = threads.active_run(thread_id)
        if run is None:
            raise threads.refusals.conflict("That question has already been answered.")
        await _in_thread(threads.resume_run, run["id"], card_id, choice)
        # The answer is kept on the run with its card (`threads.resume_run`); the run
        # reads it there, so what it was shown is what it acts on.
        self._launch(thread_id, run["id"], Command(resume=choice))
        return threads.get_run(run["id"])

    async def _undo(self, run: dict[str, Any], to: str) -> None:
        task = self.tasks.get(run["id"])
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await _in_thread(threads.stop_run, run["id"], to)
        await checkpoints.restore(self.saver, run["thread_id"], run["checkpoint_id"])
        await _in_thread(self._add_event, run["id"], "done", {"state": to})

    async def stop(self, thread_id: str) -> None:
        """Back to before the turn. The taint and the egress log stay."""
        run = threads.active_run(thread_id)
        if run is None:
            raise threads.refusals.conflict("This answer has already finished.")
        await self._undo(run, threads.STOPPED)

    async def delete_thread(self, thread_id: str) -> None:
        await _in_thread(threads.delete, thread_id)
        await checkpoints.delete(self.saver, thread_id)

    async def wait_for_event(self, run_id: str, seconds: float) -> None:
        signal = self.signals.get(run_id)
        if signal is None:
            await asyncio.sleep(seconds)
            return
        try:
            await asyncio.wait_for(signal.wait(), seconds)
            signal.clear()
        except TimeoutError:
            return


KEY_REFUSED = models.KeyRefused
ID_NEEDED = models.IdNeeded
KEY_NOT_TESTED = (models.NoConnection, models.CloudRefused)


class KeyChanged(Exception):
    """The key or its ID was saved, changed or removed while it was tested, so the
    test's answer is about a key that's gone: it changes nothing."""


KEY_CHANGED = KeyChanged
# Each provider's key changes, counted: a test stores its key only if none happened
# while it waited for the provider. One lock orders the stores and removals.
_KEYS = threading.Lock()
_key_changes: dict[str, int] = {}


def _key_changed(provider: str) -> None:
    _key_changes[provider] = _key_changes.get(provider, 0) + 1


# Each new key its test found needs its ID, by a digest, never the key: it isn't
# stored until it works, so the needs-ID mark it earns is kept here until then, one
# for each key tried.
_needs_its_id: dict[str, set[str]] = {}


def _digest(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def local_ready(job: str = settings.LOCAL_ANSWERS) -> bool:
    return models.local_ready(job)


def local_tag(job: str = settings.LOCAL_ANSWERS) -> str:
    return models.local_tag(job)


OLLAMA_FRESH_SECONDS = 5.0


def refresh_ollama() -> None:
    """Gives `model_store` Ollama's list, so policy can read it without the agent: at
    most every few seconds, and never a real Ollama under the checks' stand-ins."""
    listed = model_store.ollama_listed_at()
    if listed is not None and time.time() - listed < OLLAMA_FRESH_SECONDS:
        return
    models.refresh_ollama_list()


def fetch_openrouter_models() -> None:
    models.fetch_openrouter_models()


def ollama_status() -> dict[str, Any]:
    return models.ollama_status()


def pull_local(tag: str, on_progress: Callable[[int, int], None]) -> None:
    models.pull_local(tag, on_progress)


def local_pull() -> model_store.Pull:
    """A download from Ollama as the route starts it: Ollama asked first, and its list
    asked again once the model is in."""
    return model_store.Pull(pull_local, bool(ollama_status()["running"]), _after_pull)


def _after_pull() -> None:
    """The model joins Ollama's list, and the work waiting for a model can go."""
    models.refresh_ollama_list()
    model_store.local_work_can_go()


class NoKeyToTest(Exception):
    """None was given, and none is saved."""


NO_KEY_TO_TEST = NoKeyToTest


def check_and_store_key(
    key: str | None,
    scope_id: str | None = None,
    provider: str = providers.ANTHROPIC,
    saved_id: bool = False,
) -> None:
    """Stored only once it has worked, with the ID it was tested with, so a refused key
    leaves the old one in place. A key that needs its Workspace or Project ID is
    recorded as needing it. With no key given, the saved one is tested, and with
    saved_id, the saved ID: both are read as the test starts, so a save or removal
    that overtakes the test, before it asks the provider or after, changes nothing."""
    found = providers.PROVIDERS[provider]
    with _KEYS:
        started = _key_changes.get(provider, 0)
        on_file = keychain.get_key(provider)
        key = key or on_file
        if saved_id and found.id_setting:
            scope_id = settings.get(found.id_setting) or None
    if not key:
        raise NoKeyToTest
    saved = on_file == key
    try:
        models.check_key(provider, key, scope_id)
    except models.IdNeeded:
        with _KEYS:
            if _key_changes.get(provider, 0) != started:
                raise KeyChanged from None
            if saved:
                policy.mark_id_refused(provider, scope_id)
            else:  # never stored yet: its mark waits until it is
                _needs_its_id.setdefault(provider, set()).add(_digest(key))
        raise
    with _KEYS:
        if _key_changes.get(provider, 0) != started:
            raise KeyChanged
        _key_changed(provider)
        keychain.set_key(key, provider)
        if found.id_setting:
            # The ID it was tested with is the one kept: an emptied one clears the old.
            settings.put(found.id_setting, scope_id or None)
        # This key's mark is settled now; another key tried keeps its own.
        tried = _needs_its_id.get(provider, set())
        learned = not saved and _digest(key) in tried
        tried.discard(_digest(key))
        policy.mark_key_worked(provider, scope_id, new_key=not saved, needs_id=learned)


def set_key_scope(provider: str, scope_id: str | None) -> None:
    """A test still waiting on the old ID won't put it back."""
    setting = providers.PROVIDERS[provider].id_setting
    if setting is None:
        return  # its key takes no ID: the route refuses first
    with _KEYS:
        _key_changed(provider)
        settings.put(setting, scope_id)


def remove_key(provider: str) -> None:
    """The key leaves the Keychain with its ID and needs-ID mark, and a test still
    waiting on it won't put it back."""
    found = providers.PROVIDERS[provider]
    with _KEYS:
        _key_changed(provider)
        _needs_its_id.pop(provider, None)
        keychain.delete_key(provider)
        if found.id_setting:
            settings.put(found.id_setting, None)
        policy.forget_id_mark(provider)


def edit_memory(memory_id: str, text: str) -> None:
    memory.edit(memory_id, text)


def delete_memory(memory_id: str) -> None:
    memory.delete(memory_id)
