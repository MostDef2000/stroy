from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis

from stroy.config import Settings


class JobDispatcher(Protocol):
    async def initialize(self) -> None: ...
    async def notify(self, job_id: str) -> None: ...
    async def ready(self) -> bool: ...
    async def close(self) -> None: ...


@dataclass
class NullJobDispatcher:
    async def initialize(self) -> None:
        return None

    async def notify(self, job_id: str) -> None:
        return None

    async def ready(self) -> bool:
        return True

    async def close(self) -> None:
        return None


class RedisJobDispatcher:
    """Best-effort wakeup channel; PostgreSQL remains the durable job source."""

    def __init__(self, url: str, channel: str) -> None:
        self.redis = Redis.from_url(url, decode_responses=True)
        self.channel = channel

    async def initialize(self) -> None:
        await self.redis.ping()

    async def notify(self, job_id: str) -> None:
        await self.redis.publish(self.channel, job_id)

    async def ready(self) -> bool:
        try:
            return bool(await self.redis.ping())
        except Exception:
            return False

    async def close(self) -> None:
        await self.redis.aclose()


def create_job_dispatcher(settings: Settings) -> JobDispatcher:
    if settings.redis_url:
        return RedisJobDispatcher(settings.redis_url, settings.redis_job_channel)
    return NullJobDispatcher()
