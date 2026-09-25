from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass
from typing import Any, Protocol

from stroy.generation import GenerationContext, finalize_generation_manifest
from stroy.rendering import RenderContext, finalize_render_manifest
from stroy.services.adapters import AdapterError
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

    async def _cancel_executor(
        self,
        executor: Executor,
        job: dict[str, Any],
    ) -> None:
        cancel = getattr(executor, "cancel", None)
        if cancel is None:
            return
        result = cancel(job)
        if asyncio.iscoroutine(result):
            await result

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

    async def run_once(
        self,
        shutdown_event: asyncio.Event | None = None,
    ) -> bool:
        if shutdown_event is not None and shutdown_event.is_set():
            return False
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
                {
                    "code": "unsupported_job_type",
                    "detail": job["job_type"],
                    "context": {},
                },
                {"worker_id": self.client.worker_id},
            )
            return True

        stop = asyncio.Event()
        renew_task: asyncio.Task | None = None
        try:
            await self.client.start(job_id, lease_id)
            await self.client.progress(
                job_id,
                lease_id,
                {"phase": "executing", "fraction": 0.0},
                {"worker_id": self.client.worker_id},
            )
            renew_task = asyncio.create_task(self._renew_lease(job_id, lease_id, stop))
            execution_task = asyncio.create_task(executor.execute(job))
            while not execution_task.done():
                if shutdown_event is not None and shutdown_event.is_set():
                    await self._cancel_executor(executor, job)
                    execution_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await execution_task
                    stop.set()
                    if renew_task:
                        renew_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await renew_task
                    await self.client.release(job_id, lease_id)
                    return True

                done, _ = await asyncio.wait(
                    {execution_task},
                    timeout=min(2.0, self.lease_renew_seconds),
                )
                if execution_task in done:
                    break

                lease_state = await self.client.lease_status(job_id, lease_id)
                if lease_state.get("status") == "cancelled":
                    await self._cancel_executor(executor, job)
                    execution_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await execution_task
                    stop.set()
                    if renew_task:
                        renew_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await renew_task
                    return True
                if not lease_state.get("lease_valid", False):
                    execution_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await execution_task
                    stop.set()
                    if renew_task:
                        renew_task.cancel()
                        with contextlib.suppress(asyncio.CancelledError):
                            await renew_task
                    return True

            result = await execution_task

            raw_artifacts = result.pop("_artifacts", [])
            if not isinstance(raw_artifacts, list):
                raise ValueError("executor _artifacts must be a list")

            output_asset_ids: list[str] = []
            semantic_asset_ids: dict[str, str] = {}
            for artifact in raw_artifacts:
                if not isinstance(artifact, dict):
                    raise ValueError("executor artifact must be an object")
                filename = artifact.get("filename")
                data = artifact.get("data")
                media_type = artifact.get("media_type", "application/octet-stream")
                if not isinstance(filename, str) or not isinstance(data, bytes):
                    raise ValueError("executor artifact requires filename and bytes")
                semantic_name = artifact.get("semantic_name")
                uploaded = await self.client.upload_output(
                    job_id,
                    lease_id,
                    filename=filename,
                    data=data,
                    media_type=str(media_type),
                    semantic_name=(
                        str(semantic_name)
                        if isinstance(semantic_name, str) and semantic_name
                        else None
                    ),
                )
                output_asset_ids.append(uploaded["id"])
                if isinstance(semantic_name, str) and semantic_name:
                    semantic_asset_ids[semantic_name] = uploaded["id"]

            raw_generation = result.pop("_generation_context", None)
            if raw_generation is not None:
                generation = GenerationContext.model_validate(raw_generation)
                workflow = result.get("workflow") or {}
                workflow_id = workflow.get("id")
                workflow_version = workflow.get("version")
                model_profile = result.get("model_profile")
                if not all(
                    isinstance(value, str) and value
                    for value in (workflow_id, workflow_version, model_profile)
                ):
                    raise ValueError(
                        "generation result requires workflow id/version and model profile"
                    )
                manifest = finalize_generation_manifest(
                    context=generation,
                    workflow_id=workflow_id,
                    workflow_version=workflow_version,
                    model_profile=model_profile,
                    output_asset_ids=output_asset_ids,
                )
                result["generation_manifest"] = manifest.model_dump(
                    mode="json",
                    exclude_none=True,
                )
            raw_render = result.pop("_render_context", None)
            if raw_render is not None:
                render_context = RenderContext.model_validate(raw_render)
                render_manifest = finalize_render_manifest(
                    context=render_context,
                    pass_asset_ids=semantic_asset_ids,
                )
                result["render_manifest"] = render_manifest.model_dump(
                    mode="json",
                    exclude_none=True,
                )
            result["output_asset_ids"] = output_asset_ids

            stop.set()
            if renew_task:
                await renew_task

            runtime_provenance = {"worker_id": self.client.worker_id}
            adapter_provenance = result.get("adapter_provenance")
            if isinstance(adapter_provenance, dict):
                runtime_provenance.update(adapter_provenance)
            await self.client.complete(
                job_id,
                lease_id,
                result,
                runtime_provenance,
            )
        except AdapterError as exc:
            stop.set()
            if renew_task:
                renew_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renew_task
            await self.client.fail(
                job_id,
                lease_id,
                exc.as_error(),
                {"worker_id": self.client.worker_id},
            )
        except Exception as exc:
            stop.set()
            if renew_task:
                renew_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await renew_task
            await self.client.fail(
                job_id,
                lease_id,
                {
                    "code": "executor_error",
                    "detail": str(exc),
                    "context": {},
                },
                {"worker_id": self.client.worker_id},
            )
        return True

    async def run_forever(
        self,
        shutdown_event: asyncio.Event | None = None,
    ) -> None:
        shutdown = shutdown_event or asyncio.Event()
        backoff = max(1.0, self.poll_seconds)
        while not shutdown.is_set():
            try:
                worked = await self.run_once(shutdown)
                backoff = max(1.0, self.poll_seconds)
                if not worked and not shutdown.is_set():
                    try:
                        await asyncio.wait_for(
                            shutdown.wait(),
                            timeout=self.poll_seconds,
                        )
                    except TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception:
                if shutdown.is_set():
                    break
                try:
                    await asyncio.wait_for(shutdown.wait(), timeout=backoff)
                except TimeoutError:
                    pass
                backoff = min(backoff * 2.0, 60.0)
