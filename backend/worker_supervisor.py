"""Starts and restarts the worker, holds its stdin, and forwards an unlock line
without reading it."""

import asyncio
import json
import logging
import sys
import time
from collections import deque
from collections.abc import Sequence

from backend import build_mode

logger = logging.getLogger(__name__)

MAX_DEATHS = 5
DEATH_WINDOW_SECONDS = 600
BACKOFF_SECONDS = 1.0


def worker_command() -> tuple[str, ...]:
    return (sys.executable, "-m", build_mode.worker_module())


class WorkerSupervisor:
    def __init__(self, command: Sequence[str] | None = None) -> None:
        self.command = tuple(command) if command else worker_command()
        self.process: asyncio.subprocess.Process | None = None
        self.deaths: deque[float] = deque()
        self.stopped_for_good = False
        self._task: asyncio.Task[None] | None = None
        self._closing = False

    @property
    def state(self) -> str:
        if self.stopped_for_good:
            return "stopped"
        running = self.process is not None and self.process.returncode is None
        return "running" if running else "starting"

    def start(self) -> None:
        self._closing = False
        self.stopped_for_good = False
        self._task = asyncio.create_task(self._supervise())

    async def _supervise(self) -> None:
        while not self._closing:
            self.process = await asyncio.create_subprocess_exec(
                *self.command, stdin=asyncio.subprocess.PIPE
            )
            await self.process.wait()
            if self._closing:
                return
            logger.warning("worker exited with %s", self.process.returncode)
            if self._too_many_deaths():
                self.stopped_for_good = True
                return
            await asyncio.sleep(BACKOFF_SECONDS)

    def _too_many_deaths(self) -> bool:
        now = time.monotonic()
        self.deaths.append(now)
        while self.deaths and now - self.deaths[0] > DEATH_WINDOW_SECONDS:
            self.deaths.popleft()
        return len(self.deaths) >= MAX_DEATHS

    async def forward_unlock(self, job_id: str, password: str) -> bool:
        """Sends one JSON line to the worker. False if no worker is listening."""
        process = self.process
        if process is None or process.returncode is not None or process.stdin is None:
            return False
        process.stdin.write(
            (json.dumps({"job_id": job_id, "password": password}) + "\n").encode()
        )
        await process.stdin.drain()
        return True

    async def stop(self) -> None:
        self._closing = True
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            await self.process.wait()
        if self._task is not None:
            await self._task
