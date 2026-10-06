"""Explicit fresh-start selection; historical homes are never moved or deleted."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Generator
from contextlib import ExitStack, contextmanager
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

from .application_paths import SpaceSelection, selected_home
from .bundle import managed_path

if TYPE_CHECKING:
    from scopecat.daemon.endpoint import DaemonEndpointRecord

    from .application_runtime import ApplicationRuntime


class ResetAttempt(SpaceSelection):
    source: str


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
                path = managed_path(runtime.home, root / name)
                # Existing unsupported stores already have their data directory.
                locks.enter_context(FileLock(path, timeout=0))
        except Timeout as error:
            raise ValueError(
                "后台仍在运行或更新中；请先退出后台，原数据保留"
            ) from error
        yield binding.data_root


def check_format(runtime: ApplicationRuntime) -> None:
    """Inspect stopped stores before package preparation or author code loading."""
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


def fresh_start(
    runtime: ApplicationRuntime,
    expected: UnsupportedDataSpace,
    prepare: Callable[[Path], None],
) -> DaemonEndpointRecord:
    """Prepare and start an empty home, then atomically select it after success.

    The caller owns the native session operation lock and explicit confirmation.
    Before commit, every failure keeps the old selection. The durable attempt
    permits retry after a crash, including stopping its own candidate service.
    No historical content or failed candidate directory is removed.
    """
    from .application_runtime import ApplicationRuntime, write_state

    with runtime.lock:
        if (
            runtime.home != selected_home(runtime.anchor)
            or expected.home != runtime.home
        ):
            raise ValueError("数据空间已改变，请重新打开应用确认")
        _standard_layout(runtime)
        with _store_ownership(runtime) as data, _worker_ownership(data):
            _require_quiet(runtime, data)
            try:
                inspect_project_schema(data / "control.sqlite3")
            except SchemaVersionError as error:
                if (error.actual, error.expected) != (
                    expected.actual,
                    expected.expected,
                ):
                    raise ValueError("数据格式已改变，请重试并重新确认") from error
            else:
                raise ValueError("当前数据格式已可用，请重试启动；未创建新空间")
            journal = managed_path(
                runtime.anchor, runtime.anchor / "reset-attempt.json"
            )
            source = str(runtime.home)
            attempt = (
                ResetAttempt.model_validate_json(journal.read_bytes())
                if journal.exists()
                else None
            )
            if attempt is None or attempt.source != source:
                attempt = ResetAttempt(space=uuid4().hex, source=source)
                write_state(journal, attempt.model_dump_json())
                _sync_directory(runtime.anchor)
            destination = managed_path(
                runtime.anchor, runtime.anchor / "spaces" / attempt.space
            )
            destination.mkdir(parents=True, exist_ok=True)
            if (destination / "current-space.json").exists():
                raise ValueError("待准备空间包含其他空间选择，无法安全继续；原件保留")
            candidate = ApplicationRuntime(destination)
            if (candidate.root / "scopecat.toml").exists():
                _standard_layout(candidate)
            try:
                # A previous host may have died after starting this candidate.
                # Only the recorded process identity may be stopped.
                if candidate.selection.exists():
                    candidate.stop()
                prepare(destination)
                record = candidate.start()
                _sync_directory(destination)
                _sync_directory(destination.parent)
                # Commit point. No fallible journal cleanup follows it: an old
                # attempt is harmless and superseded on the next explicit reset.
                write_state(
                    runtime.anchor / "current-space.json",
                    SpaceSelection(space=attempt.space).model_dump_json(),
                )
            except BaseException:
                if candidate.selection.exists():
                    candidate.stop()
                raise
            runtime.home = candidate.home
            runtime.root = candidate.root
            runtime.selection = candidate.selection
            runtime.pending = candidate.pending
            try:
                _sync_directory(runtime.anchor)
            except OSError as error:
                raise ValueError(
                    "新空间已选中，但无法确认选择已落盘；旧原件保留。请重试启动。"
                ) from error
            return record
