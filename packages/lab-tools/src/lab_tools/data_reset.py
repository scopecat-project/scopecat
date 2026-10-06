"""Explicit, in-place reset of the standard application scientific store."""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import stat
import tempfile
from collections.abc import Callable, Generator
from contextlib import ExitStack, closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import psutil
from filelock import FileLock, Timeout

from scopecat.runtime_binding import RUNTIME_BINDING_NAME, load_runtime_binding
from scopecat_server.services.project_workers import WorkerProcess
from scopecat_server.storage.sqlite.project_store import (
    SchemaVersionError,
    inspect_project_schema,
)

from .bundle import file_hash, managed_path

if TYPE_CHECKING:
    from scopecat.daemon.endpoint import DaemonEndpointRecord

    from .application_runtime import ApplicationRuntime


class UnsupportedDataSpace(ValueError):
    """Host-readable format failure, independent of backend readiness."""

    def __init__(self, home: Path, error: SchemaVersionError):
        self.home = home
        self.actual = error.actual
        self.expected = error.expected
        super().__init__(
            f"数据格式 {self.actual if self.actual is not None else '未知旧格式'} "
            f"不受此版本支持（需要 {self.expected}）。原数据保留在 {home}。"
        )


class ResetIncomplete(UnsupportedDataSpace):
    def __init__(self, home: Path):
        super().__init__(home, SchemaVersionError("reset interrupted"))
        self.args = (
            "上次数据删除未完成，可能已有部分数据被删除；请确认继续删除并初始化。",
        )


@contextmanager
def _store_ownership(runtime: ApplicationRuntime) -> Generator[Path]:
    """Fence normal service starts without opening the original SQLite writable."""
    binding = load_runtime_binding(runtime.root)
    with ExitStack() as locks:
        try:
            for root, name in (
                (binding.deployment_root, "deployment.lock"),
                (binding.data_root, "daemon.lock"),
            ):
                # Ordinary startup also supports explicitly bound external roots.
                # Fresh start rejects those layouts before entering this guard.
                owner = runtime.home if root.is_relative_to(runtime.home) else root
                path = managed_path(owner, root / name)
                # Existing unsupported stores already have their data directory.
                locks.enter_context(FileLock(path, timeout=0))
        except Timeout as error:
            raise ValueError(
                "后台仍在运行或更新中；请先退出后台，原数据保留"
            ) from error
        yield binding.data_root


def check_format(runtime: ApplicationRuntime) -> None:
    """Inspect stopped stores before package preparation or author code loading."""
    if (runtime.home / "data-reset.json").exists():
        raise ResetIncomplete(runtime.home)
    binding = load_runtime_binding(runtime.root)
    database = binding.data_root / "control.sqlite3"
    if not database.exists():
        return
    with runtime.lock:
        if runtime.status().state in ("running", "degraded"):
            return
        # A live service owns inspection; don't copy its moving WAL.
        try:
            with _store_ownership(runtime):
                inspect_project_schema(database)
        except SchemaVersionError as error:
            raise UnsupportedDataSpace(runtime.home, error) from error


def _standard_layout(runtime: ApplicationRuntime) -> None:
    if (runtime.root / RUNTIME_BINDING_NAME).exists():
        raise ValueError(
            "此空间使用自定义数据/部署绑定，无法安全重新开始；原件保留，请联系维护者"
        )
    managed_path(runtime.home, runtime.root / ".scopecat" / "control.sqlite3")
    if runtime.root.is_junction() or (runtime.root / ".scopecat").is_junction():
        raise ValueError("拒绝重置重定向目录")
    declaration = "[lab]\n[authors]\ndependencies = []\n"
    if (runtime.root / "scopecat.toml").read_text() != declaration:
        raise ValueError("此空间包含自定义应用配置，无法安全重新开始；源码和数据保留")


@contextmanager
def _worker_ownership(data: Path) -> Generator[None]:
    directory = managed_path(data, data / "worker-diagnostics")
    with ExitStack() as locks:
        if directory.exists():
            try:
                locks.enter_context(
                    FileLock(
                        managed_path(data, directory / ".retention.lock"), timeout=0
                    )
                )
                for lease in directory.glob("*.jsonl.lock"):
                    locks.enter_context(FileLock(managed_path(data, lease), timeout=0))
            except Timeout as error:
                raise ValueError(
                    "仍有设备工作进程；请等待其退出，原数据保留"
                ) from error
        yield


def _require_quiet(runtime: ApplicationRuntime, data: Path) -> None:
    status = runtime.status()
    if status.state != "stopped":
        raise ValueError("无法确认后台已停止；请先停止后台并重试，原数据保留")
    # A diagnostic generation can exhaust its bounded log slots. Its process
    # still has the explicit project root in the fixed worker entry request.
    for process in psutil.process_iter():
        try:
            command = process.cmdline()
        except psutil.NoSuchProcess:
            continue
        except psutil.AccessDenied:
            # Unrelated OS processes are not owned by this application. Known
            # worker receipts and leases below remain mandatory ownership checks.
            continue
        if "scopecat_server.instruments.worker_process" in command:
            request = cast("dict[str, str]", json.loads(command[-1]))
            if Path(request["root"]).resolve() == runtime.root:
                raise ValueError("仍有设备工作进程；请等待其退出，原数据保留")
    workers = managed_path(data, data / "procedure-workers")
    for receipt in workers.glob("*/process.json"):
        managed_path(data, receipt)
        identity = WorkerProcess.model_validate_json(receipt.read_bytes())
        try:
            process = psutil.Process(identity.pid)
            if process.create_time() == identity.created and process.is_running():
                raise ValueError("仍有实验工作进程；请等待其退出后重试，原数据保留")
        except psutil.NoSuchProcess:
            pass
        except psutil.AccessDenied as error:
            raise ValueError("无法确认实验工作进程已退出；原数据保留") from error


def _sync_directory(path: Path) -> None:
    # Windows uses the atomic replace provided by the filesystem. Directory
    # fsync is available on Unix; propagate failures before selection commits.
    if os.name != "nt":
        descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


_FILES = (
    "control.sqlite3",
    "control.sqlite3-wal",
    "control.sqlite3-shm",
    "control.sqlite3-journal",
)


def _raise_walk_error(error: OSError) -> None:
    raise error


def _store_inventory(targets: list[Path]) -> tuple[list[Path], list[Path]]:
    files: list[Path] = []
    directories: list[Path] = []
    for target in targets:
        if target.is_dir():
            for directory, _, names in os.walk(
                target, topdown=False, onerror=_raise_walk_error
            ):
                files.extend(Path(directory) / name for name in names)
                directories.append(Path(directory))
        elif target.exists():
            files.append(target)
    return files, directories


def _deletion_targets(runtime: ApplicationRuntime, data: Path) -> list[Path]:
    """Fixed store ownership, refusing redirected or unknown object content."""
    targets = [data / name for name in _FILES] + [data / "objects"]
    for path in targets:
        managed_path(runtime.home, path)
        if path.is_junction():
            raise ValueError(f"拒绝重置重定向目录：{path}")
        if (
            path.exists()
            and path.name != "objects"
            and (not path.is_file() or path.stat().st_nlink != 1)
        ):
            raise ValueError(f"数据库目标不是普通文件：{path}")
    objects = data / "objects"
    if objects.exists():
        if not objects.is_dir():
            raise ValueError("对象存储不是目录")
        for directory, dirs, files in os.walk(
            objects, followlinks=False, onerror=_raise_walk_error
        ):
            for name in [*dirs, *files]:
                path = Path(directory) / name
                managed_path(runtime.home, path)
                if path.is_junction():
                    raise ValueError(f"拒绝重置重定向目录：{path}")
                relative = path.relative_to(objects).as_posix()
                prefix = r"(?:resources/[a-z_]+/[0-9a-f]{64}/)?"
                if path.is_dir():
                    valid = re.fullmatch(
                        r"resources(?:/[a-z_]+(?:/[0-9a-f]{64})?)?|"
                        + prefix
                        + r"[0-9a-f]{2}",
                        relative,
                    )
                else:
                    valid = stat.S_ISREG(path.stat().st_mode) and re.fullmatch(
                        prefix + r"(?:[0-9a-f]{2}/(?:[0-9a-f]{62}|"
                        r"\.[0-9a-f]{64}\.[0-9a-f]{32}\.tmp)|\.import-[0-9a-f]{32}\.tmp)",
                        relative,
                    )
                if not valid:
                    raise ValueError(f"对象目录含未知内容，未删除：{path}")
    journal = data / "control.sqlite3-journal"
    if journal.exists() and journal.stat().st_size:
        raise ValueError(
            "检测到未完成的 SQLite rollback journal；无法安全原样备份，未删除"
        )
    return targets


def _safe_path(path: Path) -> Path:
    path = path.absolute()
    managed_path(Path(path.anchor), path)
    if any(parent.is_junction() for parent in (path, *path.parents)):
        raise ValueError("备份路径不能包含重定向目录")
    return path.resolve()


def verify_archive(archive: Path, manifest_hash: str) -> dict[str, str]:
    _safe_path(archive)
    manifest = _safe_path(archive / "manifest.json")
    if file_hash(manifest) != manifest_hash:
        raise ValueError("备份清单已改变；未继续删除")
    document = cast("dict[str, object]", json.loads(manifest.read_bytes()))
    files = cast("dict[str, str]", document["files"])
    for name, digest in files.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("非法备份路径")
        path = _safe_path(archive / "store" / relative)
        if file_hash(path) != digest:
            raise ValueError(f"备份校验失败：{name}")
    return files


def backup_store(
    data: Path, targets: list[Path], destination: Path, home: Path
) -> tuple[Path, str]:
    """Opaque stopped-store archive, not a current-format restore promise."""
    destination = _safe_path(destination)
    if destination.is_relative_to(home.resolve()) or not destination.is_dir():
        raise ValueError("请选择应用数据目录之外的现有备份目录")
    archive = destination / (
        "Scopecat-data-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex
    )
    files, _ = _store_inventory(targets)
    before = {str(p.relative_to(data)): file_hash(p) for p in files}
    with tempfile.TemporaryDirectory(
        prefix=".scopecat-backup-", dir=destination
    ) as temporary:
        staged = Path(temporary) / "archive"
        (staged / "store").mkdir(parents=True)
        for path in files:
            target = staged / "store" / path.relative_to(data)
            target.parent.mkdir(parents=True, exist_ok=True)
            with path.open("rb") as source, target.open("xb") as output:
                shutil.copyfileobj(source, output)
                output.flush()
                os.fsync(output.fileno())
        manifest = {
            "kind": "scopecat-opaque-store-originals",
            "created_at": datetime.now(UTC).isoformat(),
            "source": str(data),
            "restore": (
                "Original format only; current-version restore compatibility "
                "is not verified. No source/environments included."
            ),
            "files": before,
        }
        from .application_runtime import write_state

        write_state(staged / "manifest.json", json.dumps(manifest, indent=2))
        digest = file_hash(staged / "manifest.json")
        verify_archive(staged, digest)
        if any(file_hash(data / name) != value for name, value in before.items()):
            raise ValueError("备份期间原数据发生改变；未删除")
        for directory, _, _ in os.walk(staged, topdown=False):
            _sync_directory(Path(directory))
        staged.rename(archive)
        _sync_directory(destination)
    return archive, digest


def reset_store(
    runtime: ApplicationRuntime,
    expected: UnsupportedDataSpace,
    prepare: Callable[[], None],
    backup_directory: Path | None,
) -> DaemonEndpointRecord:
    """Delete only the confirmed store, then initialize in place. No rollback."""
    from scopecat_server.storage.sqlite.connection import SQLiteDatabase
    from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore

    from .application_runtime import write_state

    with runtime.lock:
        if expected.home != runtime.home:
            raise ValueError("应用目录已改变，请重新确认")
        _standard_layout(runtime)
        with _store_ownership(runtime) as data, _worker_ownership(data):
            _require_quiet(runtime, data)
            targets = _deletion_targets(runtime, data)
            marker = managed_path(runtime.home, runtime.home / "data-reset.json")
            receipt: dict[str, str | None] = {
                "backup": None,
                "manifest_hash": None,
                "phase": "deleting",
            }
            if marker.exists():
                receipt = cast("dict[str, str | None]", json.loads(marker.read_bytes()))
                if set(receipt) != {"backup", "manifest_hash", "phase"} or receipt[
                    "phase"
                ] not in ("deleting", "initializing"):
                    raise ValueError("未知重置记录；未删除数据")
                if receipt["backup"] is not None:
                    verify_archive(
                        Path(receipt["backup"]),
                        cast("str", receipt["manifest_hash"]),
                    )
            else:
                try:
                    inspect_project_schema(data / "control.sqlite3")
                except SchemaVersionError as error:
                    if (error.actual, error.expected) != (
                        expected.actual,
                        expected.expected,
                    ):
                        raise ValueError("数据格式已改变，请重新确认") from error
                else:
                    raise ValueError("当前数据格式已可用；未删除数据，请重试启动")
            if not marker.exists() or (
                receipt["backup"] is None and backup_directory is not None
            ):
                archive = None
                digest = None
                # Hold the SQLite writer across the raw archive; never checkpoint.
                with ExitStack() as guards:
                    if targets[0].exists():
                        guard = guards.enter_context(
                            closing(
                                sqlite3.connect(
                                    targets[0], timeout=0, isolation_level=None
                                )
                            )
                        )
                        guard.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
                        guard.execute("BEGIN IMMEDIATE")
                        guards.callback(guard.rollback)
                    if backup_directory is not None:
                        archive, digest = backup_store(
                            data, targets, backup_directory, runtime.home
                        )
                receipt = {
                    "backup": str(archive) if archive else None,
                    "manifest_hash": digest,
                    "phase": "deleting",
                }
                write_state(marker, json.dumps(receipt))
                _sync_directory(runtime.home)
            try:
                if targets[0].exists():
                    with closing(
                        sqlite3.connect(targets[0], timeout=0, isolation_level=None)
                    ) as guard:
                        guard.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
                        guard.execute("BEGIN IMMEDIATE")
                        guard.rollback()
                files, directories = _store_inventory(targets)
                if receipt["phase"] == "deleting" and receipt["backup"] is not None:
                    archived = verify_archive(
                        Path(receipt["backup"]),
                        cast("str", receipt["manifest_hash"]),
                    )
                    for path in files:
                        if archived.get(str(path.relative_to(data))) != file_hash(path):
                            raise ValueError("待删除内容不在已校验备份中，拒绝继续")
                if receipt["phase"] == "initializing" and any(
                    (data / "objects").iterdir() if (data / "objects").exists() else ()
                ):
                    raise ValueError("初始化期间出现未知对象；拒绝删除")
                for path in files:
                    path.unlink()
                for path in directories:
                    path.rmdir()
                _sync_directory(data)
                receipt["phase"] = "initializing"
                write_state(marker, json.dumps(receipt))
                _sync_directory(runtime.home)
                store = SQLiteProjectStore(
                    SQLiteDatabase(data / "control.sqlite3"), data / "objects"
                )
                try:
                    store.bootstrap()
                finally:
                    store.close()
                marker.unlink()
                _sync_directory(runtime.home)
            except Exception as error:
                raise ValueError(
                    "删除或初始化未完成，可能已有部分数据被删除，无法回滚；请保留日志并重新确认继续。"
                ) from error
    # Release ownership before native preparation constructs its own runtime lock.
    prepare()
    return runtime.start()
