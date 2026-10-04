from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Protocol

import boto3
from botocore.exceptions import ClientError

from stroy.config import Settings


class ObjectStore(Protocol):
    async def initialize(self) -> None: ...
    async def ready(self) -> bool: ...
    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None: ...
    async def get_bytes(self, key: str) -> bytes: ...
    async def delete_prefix(self, prefix: str) -> int: ...
    async def presign_get(self, key: str, expires_seconds: int = 900) -> str | None: ...
    async def presign_put(
        self, key: str, media_type: str, expires_seconds: int = 900
    ) -> str | None: ...


@dataclass
class MemoryObjectStore:
    objects: dict[str, bytes] = field(default_factory=dict)

    async def initialize(self) -> None:
        return None

    async def ready(self) -> bool:
        return True

    async def put_bytes(self, key: str, data: bytes, media_type: str) -> None:
        self.objects[key] = data

    async def get_bytes(self, key: str) -> bytes:
        return self.objects[key]

    async def delete_prefix(self, prefix: str) -> int:
        keys = [key for key in self.objects if key.startswith(prefix)]
        for key in keys:
            del self.objects[key]
        return len(keys)

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

    async def initialize(self) -> None:
        try:
            await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)
        except ClientError:
            await asyncio.to_thread(self.client.create_bucket, Bucket=self.bucket)

    async def ready(self) -> bool:
        try:
            await asyncio.to_thread(self.client.head_bucket, Bucket=self.bucket)
            return True
        except ClientError:
            return False

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

    async def delete_prefix(self, prefix: str) -> int:
        def _delete() -> int:
            paginator = self.client.get_paginator("list_objects_v2")
            deleted = 0
            for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
                keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
                if keys:
                    self.client.delete_objects(
                        Bucket=self.bucket, Delete={"Objects": keys}
                    )
                    deleted += len(keys)
            return deleted

        return await asyncio.to_thread(_delete)

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
