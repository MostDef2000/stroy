from __future__ import annotations

import asyncio
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
    ) -> None:
        self.client = client
        self.executors = executors
        self.poll_seconds = poll_seconds

    async def run_once(self) -> bool:
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
        try:
            await self.client.start(job_id, lease_id)
            result = await executor.execute(job)
            await self.client.complete(job_id, lease_id, result)
        except Exception as exc:
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
