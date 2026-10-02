"""Object storage for derived media (frames, extracted audio, thumbnails).

Two backends behind one interface: local filesystem for development and
S3-compatible for deployment. Collected source media is treated as cache, not
as a record of truth -- the record of truth is the source URL plus provenance in
Postgres -- so storage loss degrades the system but never invalidates findings.
"""

from __future__ import annotations

import asyncio
import hashlib
import mimetypes
from abc import ABC, abstractmethod
from pathlib import Path

import structlog

from aypo.config import get_settings

log = structlog.get_logger(__name__)


def content_key(prefix: str, data: bytes, suffix: str) -> str:
    """Content-addressed key, so re-processing an item overwrites rather than
    accumulating duplicates and identical frames deduplicate for free."""
    return f"{prefix}/{hashlib.sha256(data).hexdigest()[:32]}{suffix}"


class Storage(ABC):
    @abstractmethod
    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str: ...

    @abstractmethod
    async def get(self, key: str) -> bytes | None: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    def url_for(self, key: str) -> str: ...


class LocalStorage(Storage):
    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        # Reject traversal: keys are generated internally but this is cheap.
        safe = Path(key)
        if safe.is_absolute() or ".." in safe.parts:
            raise ValueError(f"unsafe storage key: {key!r}")
        return self.root / safe

    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)
        return key

    async def get(self, key: str) -> bytes | None:
        path = self._path(key)
        if not path.exists():
            return None
        return await asyncio.to_thread(path.read_bytes)

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()

    def url_for(self, key: str) -> str:
        return self._path(key).as_uri()


class S3Storage(Storage):
    def __init__(self, bucket: str, region: str | None, access_key: str, secret_key: str) -> None:
        import boto3

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )

    async def put(self, key: str, data: bytes, *, content_type: str | None = None) -> str:
        ct = content_type or mimetypes.guess_type(key)[0] or "application/octet-stream"
        await asyncio.to_thread(
            self._client.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=ct
        )
        return key

    async def get(self, key: str) -> bytes | None:
        def _get() -> bytes | None:
            try:
                return self._client.get_object(Bucket=self.bucket, Key=key)["Body"].read()
            except self._client.exceptions.NoSuchKey:
                return None

        return await asyncio.to_thread(_get)

    async def exists(self, key: str) -> bool:
        def _head() -> bool:
            try:
                self._client.head_object(Bucket=self.bucket, Key=key)
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_head)

    def url_for(self, key: str) -> str:
        return self._client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=3600
        )


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is not None:
        return _storage
    settings = get_settings()
    if settings.aypo_storage_backend == "s3":
        bucket = settings.s3_bucket
        access = settings.secret("aws_access_key_id")
        secret = settings.secret("aws_secret_access_key")
        if bucket and access and secret:
            _storage = S3Storage(bucket, settings.aws_region, access, secret)
            log.info("storage.s3", bucket=bucket, region=settings.aws_region)
            return _storage
        log.warning(
            "storage.s3_unconfigured_falling_back_to_local",
            missing=settings.missing_for("storage"),
        )
    root = settings.aypo_local_storage_root
    if not root.is_absolute():
        root = settings.repo_root / root
    _storage = LocalStorage(root)
    log.info("storage.local", root=str(root))
    return _storage
