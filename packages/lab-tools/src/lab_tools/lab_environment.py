"""Prepare a retained laboratory delivery at its final project environment path."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from contextlib import ExitStack, suppress
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import psutil
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict

from scopecat.project import load_project
from scopecat_server.lifecycle import inspect_daemon

from . import bundle as delivery


@dataclass(frozen=True, slots=True)
class PreparedEnvironment:
    python: Path
    gui: Path


class _Attempt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    token: str
    project: str
    launching: bool = False
    pid: int | None = None
    created: float | None = None


def _save(path: Path, attempt: _Attempt) -> None:
    descriptor, name = tempfile.mkstemp(prefix=".attempt-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(attempt.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


def _require_finished(attempt: _Attempt) -> None:
    if attempt.launching:
        raise ValueError(
            "上次安装在记录子进程前中断；请先人工确认安装进程已退出，保留原环境"
        )
    if attempt.pid is None or attempt.created is None:
        return
    try:
        child = psutil.Process(attempt.pid)
        if (
            abs(child.create_time() - attempt.created) < 0.01
            and child.status() != psutil.STATUS_ZOMBIE
        ):
            raise ValueError("上次安装子进程仍在运行，请等待其退出后重试；没有终止进程")
    except psutil.NoSuchProcess:
        pass


def prepare_environment(project: Path, bundle: Path, home: Path) -> PreparedEnvironment:
    """Prepare only our environment; retain interrupted attempts without deletion."""
    project = project.resolve()
    selected = load_project(project / "scopecat.toml", resolve_adapter=False)
    home = home.resolve()
    attempts = home / "environment-attempts"
    attempts.mkdir(parents=True, exist_ok=True)
    key = sha256(str(project).encode("utf-8")).hexdigest()
    attempt_file = attempts / f"{key}.json"
    environment = project / ".venv"
    try:
        with ExitStack() as stack:
            stack.enter_context(FileLock(attempts / f"{key}.lock", timeout=0))
            binding = selected.runtime_binding
            for path in (
                binding.deployment_root / "deployment.lock",
                binding.data_root / "daemon.lock",
            ):
                path.parent.mkdir(parents=True, exist_ok=True)
                stack.enter_context(FileLock(path, timeout=0))
            if inspect_daemon(selected).state != "stopped":
                raise ValueError("准备实验环境前请先停止实验服务")
            retained = delivery.retain_bundle(bundle, home)
            attempt = (
                _Attempt.model_validate_json(attempt_file.read_text(encoding="utf-8"))
                if attempt_file.exists()
                else None
            )
            if attempt is not None:
                if attempt.project != str(project):
                    raise ValueError("环境准备记录不属于此代码目录，原环境保留")
                _require_finished(attempt)
            if environment.is_symlink():
                raise ValueError("已有 .venv 是外部链接；不会修改或替换它")
            if environment.exists():
                marker = environment / delivery.OWNERSHIP
                if (
                    attempt is None
                    or not marker.is_file()
                    or marker.read_text(encoding="utf-8") != attempt.token
                ):
                    raise ValueError(
                        "已有 .venv 不属于本应用准备流程；请沿用它或选择新的代码目录"
                    )
                receipt = environment / delivery.RECEIPT
                if receipt.exists():
                    delivery.check_receipt(environment, retained)
                    return _prepared(environment, retained)
                failed = project / f".venv-failed-{uuid4().hex}"
                environment.rename(failed)
                print(f"已保留上次未完成的实验环境: {failed}", flush=True)
            attempt = _Attempt(token=uuid4().hex, project=str(project))
            _save(attempt_file, attempt)

            def process_started(pid: int | None) -> None:
                attempt.launching = pid is None
                attempt.pid = pid
                attempt.created = None
                if pid is not None:
                    with suppress(psutil.NoSuchProcess):
                        attempt.created = psutil.Process(pid).create_time()
                _save(attempt_file, attempt)

            delivery.install_bundle(
                retained,
                environment,
                ownership_token=attempt.token,
                on_process=process_started,
            )
            delivery.check_receipt(environment, retained)
            return _prepared(environment, retained)
    except Timeout as error:
        raise ValueError(
            "环境准备或实验服务正在运行；请完成操作并停止服务后重试"
        ) from error


def _prepared(environment: Path, retained: Path) -> PreparedEnvironment:
    python = environment / (
        "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    )
    if not python.is_file():
        raise ValueError("已完成环境的 Python 不存在；保留原环境，请使用新的代码目录")
    checked = subprocess.run(  # noqa: S603 - fixed read-only target-runtime check
        [
            str(python),
            "-I",
            "-c",
            (
                "import sys; from pathlib import Path; "
                "from lab_tools.bundle import gui_directory; "
                "from lab_tools.project import environment_identity; "
                "assert gui_directory(None, environment_identity()) "
                "== Path(sys.argv[1])"
            ),
            str(retained / "gui"),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    if checked.returncode != 0:
        raise ValueError(
            "已完成环境与交付 GUI 的身份检查失败；原环境保留，不会原地重装："
            + checked.stderr.strip()
        )
    return PreparedEnvironment(python=python, gui=retained / "gui")
