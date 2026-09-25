import asyncio

import pytest

from stroy.worker.runtime import WorkerRunner


class FakeClient:
    def __init__(self) -> None:
        self.heartbeats = 0
        self.renews = 0
        self.started = 0
        self.completed = 0
        self.failed = 0
        self.progress_updates = 0
        self.worker_id = "worker-test"

    async def heartbeat(self) -> None:
        self.heartbeats += 1

    async def claim(self):
        return {
            "job_id": "job-1",
            "lease_id": "lease-1",
            "job_type": "slow",
            "payload": {},
        }

    async def start(self, job_id: str, lease_id: str) -> None:
        self.started += 1

    async def renew(self, job_id: str, lease_id: str) -> None:
        self.renews += 1

    async def progress(
        self,
        job_id: str,
        lease_id: str,
        progress: dict,
        runtime_provenance: dict | None = None,
    ) -> None:
        self.progress_updates += 1

    async def complete(
        self,
        job_id: str,
        lease_id: str,
        result: dict,
        runtime_provenance: dict | None = None,
    ) -> None:
        self.completed += 1

    async def fail(
        self,
        job_id: str,
        lease_id: str,
        error: dict,
        runtime_provenance: dict | None = None,
    ) -> None:
        self.failed += 1


class SlowExecutor:
    async def execute(self, job: dict) -> dict:
        await asyncio.sleep(0.04)
        return {"ok": True}


@pytest.mark.asyncio
async def test_worker_renews_lease_while_executor_runs() -> None:
    client = FakeClient()
    runner = WorkerRunner(
        client,
        {"slow": SlowExecutor()},
        heartbeat_seconds=0.001,
        lease_renew_seconds=0.005,
    )

    assert await runner.run_once() is True
    assert client.heartbeats == 1
    assert client.started == 1
    assert client.progress_updates == 1
    assert client.renews >= 1
    assert client.completed == 1
    assert client.failed == 0
