"""Immutable content-addressed objects used by the SQLite run index."""

from __future__ import annotations

import hashlib
import os
import re
from collections import OrderedDict
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from uuid import uuid4

_DIGEST = re.compile(r"^sha256:([0-9a-f]{64})$")


class ObjectStoreError(OSError):
    """Base object-store I/O failure."""


class ObjectNotFoundError(ObjectStoreError):
    """An indexed object is absent."""


class ObjectCorruptError(ObjectStoreError):
    """An object's bytes do not match its digest."""


@dataclass(frozen=True, slots=True)
class StoredObject:
    digest: str


class ImmutableObjectStore:
    """SHA-256 objects published once and never changed in place."""

    def __init__(self, root: str | Path, *, read_cache_bytes: int = 16 * 2**20) -> None:
        self.root = Path(root)
        self._read_cache_bytes = read_cache_bytes
        self._cached_bytes = 0
        self._read_cache: OrderedDict[str, tuple[os.stat_result, bytes]] = OrderedDict()
        self._read_lock = Lock()

    def bootstrap(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes) -> StoredObject:
        hexdigest = hashlib.sha256(content).hexdigest()
        digest = f"sha256:{hexdigest}"
        path = self.root / hexdigest[:2] / hexdigest[2:]
        if path.is_file():
            return StoredObject(digest=digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.parent / f".{hexdigest}.{uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            with suppress(FileExistsError):
                os.link(temporary, path)
            _fsync_directory(path.parent)
        except OSError as error:
            raise ObjectStoreError(path) from error
        finally:
            with suppress(OSError):
                temporary.unlink(missing_ok=True)
        return StoredObject(digest=digest)

    def read(self, digest: str) -> bytes:
        path = self.path_for(digest)
        try:
            content = path.read_bytes()
        except FileNotFoundError as error:
            raise ObjectNotFoundError(path) from error
        except OSError as error:
            raise ObjectStoreError(path) from error
        if f"sha256:{hashlib.sha256(content).hexdigest()}" != digest:
            raise ObjectCorruptError(path)
        return content

    def path_for(self, digest: str) -> Path:
        match = _DIGEST.fullmatch(digest)
        if match is None:
            raise ObjectCorruptError(f"invalid object digest: {digest}")
        hexdigest = match.group(1)
        return self.root / hexdigest[:2] / hexdigest[2:]

    def verify(self, digest: str) -> None:
        """Verify stored bytes without retaining the entire object in memory."""
        path = self.path_for(digest)
        try:
            with path.open("rb") as content:
                actual = hashlib.file_digest(content, "sha256").hexdigest()
        except FileNotFoundError as error:
            raise ObjectNotFoundError(path) from error
        except OSError as error:
            raise ObjectStoreError(path) from error
        if f"sha256:{actual}" != digest:
            raise ObjectCorruptError(path)

    def read_cached(self, digest: str) -> bytes:
        """Reuse verified immutable bytes with bounded retained payload and entries.

        Cache hits still stat the object. Changed/deleted files cannot silently
        reuse the old entry; misses retain the normal SHA-256 verification.
        The bound excludes bytes retained by callers and in-flight reads.
        """
        path = self.path_for(digest)
        with self._read_lock:
            try:
                before = path.stat()
            except FileNotFoundError as error:
                self._discard_cached(digest)
                raise ObjectNotFoundError(path) from error
            except OSError as error:
                raise ObjectStoreError(path) from error
            cached = self._read_cache.get(digest)
            if cached is not None and _same_object_version(cached[0], before):
                self._read_cache.move_to_end(digest)
                return cached[1]
            self._discard_cached(digest)
            content = self.read(digest)
            try:
                after = path.stat()
            except OSError:
                # The verified bytes remain usable, but do not cache a file
                # that disappeared or changed while it was being read.
                return content
            if len(content) <= self._read_cache_bytes and _same_object_version(
                before, after
            ):
                while self._read_cache and (
                    self._cached_bytes + len(content) > self._read_cache_bytes
                    or len(self._read_cache) >= 32
                ):
                    _, (_, evicted) = self._read_cache.popitem(last=False)
                    self._cached_bytes -= len(evicted)
                if self._read_cache_bytes > 0:
                    self._read_cache[digest] = (after, content)
                    self._cached_bytes += len(content)
            return content

    def _discard_cached(self, digest: str) -> None:
        cached = self._read_cache.pop(digest, None)
        if cached is not None:
            self._cached_bytes -= len(cached[1])


def _same_object_version(left: os.stat_result, right: os.stat_result) -> bool:
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        left.st_mtime_ns,
        left.st_ctime_ns,
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        right.st_mtime_ns,
        right.st_ctime_ns,
    )


def _fsync_directory(path: Path) -> None:
    # Windows does not allow directories to be opened through ``os.open``.
    # The object itself is flushed above; directory fsync is an additional
    # POSIX durability barrier for publishing the hard link.
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
