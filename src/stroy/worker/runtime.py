from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any, Protocol

from stroy.worker.client import WorkerClient


class Executor(Protocol):
    async def execute(self, job: dict[str, Any]) -> dict[str, Any]: ...


@dataclass
class FakeExecutor:
    executor_name: str

    async def execute(self, job: dict[str, Any]) -> dict[str, Any]:
        return {
            "fake": True,
            "executor": self.executor_name,
            "job_type": job["job_type"],
            "payload": job.get("payload", {}),
        }


class WorkerRunner:
    def __init__(
        self,
        client: WorkerClient,
        executors: dict[str, Executor],
        *,
        poll_seconds: float = 5.0,
        heartbeat_seconds: float = 20.0,
        lease_renew_seconds: float = 20.0,
    ) -> None:
        self.client = client
        self.executors = executors
        self.poll_seconds = poll_seconds
        self.heartbeat_seconds = heartbeat_seconds
        self.lease_renew_seconds = lease_renew_seconds
        self._last_worker_heartbeat = 0.0

    async def _heartbeat_if_due(self) -> None:
        now = time.monotonic()
        if now - self._last_worker_heartbeat >= self.heartbeat_seconds:
            await self.client.heartbeat()
            self._last_worker_heartbeat = now

    async def _renew_lease(
        self,
        job_id: str,
        lease_id: str,
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.lease_renew_seconds)
                return
            except TimeoutError:
                await self.client.renew(job_id, lease_id)

    async def run_once(self) -> bool:
        await self._heartbeat_if_due()
        job = await self.client.claim()
        if job is None:
            return False

        job_id = job["job_id"]
        lease_id = job["lease_id"]
        executor = self.executors.get(job["job_type"])
        if executor is None:
            await self.client.fail(
                job_id,
                lease_id,
                {"code": "unsupported_job_type", "detail": job["job_type"]},
            )
            return True

        stop = asyncio.Event()
        renew_task: asyncio.Task | None = None
        try:
            await self.client.start(job_id, lease_id)
            renew_task = asyncio.create_task(self._renew_lease(job_id, lease_id, stop))
            result = await executor.execute(job)
            stop.set()
            if renew_task:
                await renew_task
            await self.client.complete(job_id, lease_id, result)
        except Exception as exc:
            stop.set()
            if renew_task:
                renew_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renew_task
            await self.client.fail(
                job_id,
                lease_id,
                {"code": "executor_error", "detail": str(exc)},
            )
        return True

    async def run_forever(self) -> None:
        while True:
            worked = await self.run_once()
            if not worked:
                await asyncio.sleep(self.poll_seconds)
