"""Object storage: local filesystem (dev/test) or S3-compatible (MinIO / S3). Never publicly readable;
audio reaches the browser only through the authenticated API."""

from __future__ import annotations

import os
import re
import tempfile
import time
from pathlib import Path
from typing import Protocol

from lq.config import get_settings

_KEY = re.compile(r"^[A-Za-z0-9_\-./]{1,300}$")


def check_key(key: str) -> str:
    if not _KEY.match(key) or ".." in key or key.startswith("/"):
        raise ValueError(f"invalid storage key: {key!r}")
    return key


class Storage(Protocol):
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None: ...
    def get(self, key: str) -> bytes: ...
    def get_range(self, key: str, start: int, end: int) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def size(self, key: str) -> int: ...
    def delete(self, key: str) -> None: ...
    def list(self, prefix: str) -> list[tuple[str, float]]: ...


class LocalStorage:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _p(self, key: str) -> Path:
        return self.root / check_key(key)

    def put(self, key, data, content_type="application/octet-stream"):
        p = self._p(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".tmp-")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp, p)  # atomic: readers never see a partial object
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def get(self, key):
        return self._p(key).read_bytes()

    def get_range(self, key, start, end):
        with open(self._p(key), "rb") as f:
            f.seek(start)
            return f.read(end - start + 1)

    def exists(self, key):
        return self._p(key).is_file()

    def size(self, key):
        return self._p(key).stat().st_size

    def delete(self, key):
        p = self._p(key)
        if p.exists():
            p.unlink()

    def list(self, prefix):
        base = self.root / prefix
        if not base.exists():
            return []
        return [
            (str(p.relative_to(self.root)), p.stat().st_mtime)
            for p in base.rglob("*")
            if p.is_file() and not p.name.startswith(".tmp-")
        ]


class S3Storage:
    def __init__(
        self, bucket: str, endpoint_url: str | None, access_key: str | None, secret_key: str | None, region: str
    ):
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=Config(s3={"addressing_style": "path"}, retries={"max_attempts": 3}),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def put(self, key, data, content_type="application/octet-stream"):
        self.client.put_object(Bucket=self.bucket, Key=check_key(key), Body=data, ContentType=content_type)

    def get(self, key):
        return self.client.get_object(Bucket=self.bucket, Key=check_key(key))["Body"].read()

    def get_range(self, key, start, end):
        return self.client.get_object(Bucket=self.bucket, Key=check_key(key), Range=f"bytes={start}-{end}")[
            "Body"
        ].read()

    def exists(self, key):
        try:
            self.client.head_object(Bucket=self.bucket, Key=check_key(key))
            return True
        except Exception:
            return False

    def size(self, key):
        return int(self.client.head_object(Bucket=self.bucket, Key=check_key(key))["ContentLength"])

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=check_key(key))

    def list(self, prefix):
        out = []
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            for o in page.get("Contents", []):
                out.append((o["Key"], o["LastModified"].timestamp()))
        return out


_storage: Storage | None = None


def get_storage() -> Storage:
    global _storage
    if _storage is None:
        s = get_settings()
        if s.storage_backend == "s3":
            st = S3Storage(s.s3_bucket, s.s3_endpoint_url, s.s3_access_key, s.s3_secret_key, s.s3_region)
            st.ensure_bucket()
            _storage = st
        else:
            _storage = LocalStorage(s.storage_local_dir)
    return _storage


def set_storage(st: Storage | None) -> None:
    global _storage
    _storage = st


def now_ts() -> float:
    return time.time()
