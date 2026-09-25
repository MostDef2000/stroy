from __future__ import annotations

from typing import Any

import httpx


class WorkerClient:
    def __init__(
        self,
        server_url: str,
        token: str,
        worker_id: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.worker_id = worker_id
        self.client = client or httpx.AsyncClient(
            timeout=120,
            headers={"Authorization": f"Bearer {token}"},
        )

    async def register(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self.client.post(f"{self.server_url}/api/v1/workers/register", json=payload)
        response.raise_for_status()
        return response.json()

    async def heartbeat(self) -> None:
        response = await self.client.post(
            f"{self.server_url}/api/v1/workers/heartbeat", json={"worker_id": self.worker_id}
        )
        response.raise_for_status()

    async def claim(self) -> dict[str, Any] | None:
        response = await self.client.post(
            f"{self.server_url}/api/v1/workers/jobs/claim", json={"worker_id": self.worker_id}
        )
        if response.status_code == 204:
            return None
        response.raise_for_status()
        return response.json()

    async def download_input(self, url: str) -> bytes:
        target = f"{self.server_url}{url}" if url.startswith("/") else url
        response = await self.client.get(target)
        response.raise_for_status()
        return response.content

    async def start(self, job_id: str, lease_id: str) -> None:
        response = await self.client.post(
            f"{self.server_url}/api/v1/workers/jobs/{job_id}/start",
            json={"worker_id": self.worker_id, "lease_id": lease_id},
        )
        response.raise_for_status()

    async def renew(self, job_id: str, lease_id: str) -> None:
        response = await self.client.post(
            f"{self.server_url}/api/v1/workers/jobs/{job_id}/heartbeat",
            json={"worker_id": self.worker_id, "lease_id": lease_id},
        )
        response.raise_for_status()

    async def upload_output(
        self,
        job_id: str,
        lease_id: str,
        *,
        filename: str,
        data: bytes,
        media_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        response = await self.client.post(
            f"{self.server_url}/api/v1/workers/jobs/{job_id}/outputs",
            data={"worker_id": self.worker_id, "lease_id": lease_id},
            files={"file": (filename, data, media_type)},
        )
        response.raise_for_status()
        return response.json()

    async def complete(self, job_id: str, lease_id: str, result: dict[str, Any]) -> None:
        response = await self.client.post(
            f"{self.server_url}/api/v1/workers/jobs/{job_id}/complete",
            json={"worker_id": self.worker_id, "lease_id": lease_id, "result": result},
        )
        response.raise_for_status()

    async def fail(self, job_id: str, lease_id: str, error: dict[str, Any]) -> None:
        response = await self.client.post(
            f"{self.server_url}/api/v1/workers/jobs/{job_id}/fail",
            json={"worker_id": self.worker_id, "lease_id": lease_id, "error": error},
        )
        response.raise_for_status()
