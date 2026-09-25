from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import boto3

from stroy.config import Settings


class ObjectStore(Protocol):
    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None: ...
    async def get_bytes(self, key: str) -> bytes: ...
    async def presign_get(self, key: str, expires_seconds: int = 900) -> str | None: ...
    async def presign_put(
        self, key: str, media_type: str, expires_seconds: int = 900
    ) -> str | None: ...


@dataclass
class MemoryObjectStore:
    objects: dict[str, bytes] = field(default_factory=dict)

    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None:
        self.objects[key] = data

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    async def presign_get(self, key: str, expires_seconds: int = 900) -> str | None:
        return None

    async def presign_put(
        self, key: str, media_type: str, expires_seconds: int = 900
    ) -> str | None:
        return None


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
        )

    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None:
        await asyncio.to_thread(
            self.client.put_object,
            Bucket=self.bucket,
            Key=key,
            Body=data,
            ContentType=media_type,
        )

    async def get_bytes(self, key: str) -> bytes:
        response = await asyncio.to_thread(self.client.get_object, Bucket=self.bucket, Key=key)
        return await asyncio.to_thread(response["Body"].read)

    async def presign_get(self, key: str, expires_seconds: int = 900) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )

    async def presign_put(
        self, key: str, media_type: str, expires_seconds: int = 900
    ) -> str:
        return await asyncio.to_thread(
            self.client.generate_presigned_url,
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": media_type},
            ExpiresIn=expires_seconds,
        )


def create_object_store(settings: Settings) -> ObjectStore:
    if settings.storage_backend == "s3":
        return S3ObjectStore(settings)
    return MemoryObjectStore()
