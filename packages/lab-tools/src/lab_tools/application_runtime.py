"""One application owner per home, independent of editable author directories."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from typing import cast

import httpx2
from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict

from scopecat.author_workspaces import (
    LocalAuthorWorkspaces,
    author_bindings_path,
    author_workspace_id,
)
from scopecat.daemon.endpoint import DaemonEndpointRecord
from scopecat.daemon.health import ApplicationActivity
from scopecat.installed_adapter import AdapterReference
from scopecat.project import open_project
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat_server.lifecycle import DaemonStatus, inspect_daemon, stop_project

from .bundle import managed_path, prepare_home


class QualifiedDrivers(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    catalog: DriverCatalog
    artifact_hash: str


class Installation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    software_home: Path
    python: Path
    static_dir: Path
    environment: dict[str, str]
    settings_identity: str | None = None
    adapter_identity: str | None = None
    drivers: QualifiedDrivers | None = None
    composition: str


def application_declaration(adapter: AdapterReference | None) -> str:
    return (
        "[lab]\n[authors]\ndependencies = []\n"
        if adapter is None
        else "[lab.adapter]\n"
        f"distribution = {json.dumps(adapter.distribution)}\n"
        f"manifest = {json.dumps(adapter.manifest)}\n"
        "[authors]\ndependencies = []\n"
    )


def runtime_command(python: Path, request: dict[str, object]) -> dict[str, object]:
    """Execute in the selected environment; retain its actionable failure text."""
    environment = dict(os.environ, PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
    for name in ("SCOPECAT_DAEMON_URL", "PYTHONHOME", "PYTHONPATH"):
        environment.pop(name, None)
    with tempfile.TemporaryDirectory(prefix="scopecat-application-") as directory:
        output = Path(directory) / "result.json"
        result = subprocess.run(  # noqa: S603 - fixed entry and structured local request
            [
                str(python),
                str(Path(__file__).with_name("service_runtime.py")),
                json.dumps(request),
                str(output),
            ],
            env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
        response = (
            cast("dict[str, object]", json.loads(output.read_bytes()))
            if output.is_file()
            else {}
        )
        if result.returncode:
            raise ValueError(response.get("error") or f"运行环境检查失败：{python}")
        return response


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        staged.replace(path)
    finally:
        staged.unlink(missing_ok=True)


class ApplicationRuntime:
    """The home selects software; its fixed runtime root owns data and sources.

    There is no service registry, preferred service, or per-source endpoint.
    Preparation may fail without changing the selected installation. Activation
    shares the daemon's ownership locks and fences starts until bindings commit.
    """

    def __init__(self, home: Path):
        self.home = home.resolve()
        self.root = managed_path(self.home, self.home / "runtime")
        self.selection = managed_path(self.home, self.home / "installation.json")
        self.pending = managed_path(self.home, self.home / "installation-pending.json")
        self.candidate = managed_path(
            self.home, self.home / "installation-candidate.json"
        )
        self.lock = FileLock(self.home / "application.lock", timeout=30)

    def installation(self) -> Installation:
        if not self.selection.is_file():
            raise ValueError("应用尚未准备，请重新运行安装命令；没有创建其他服务")
        return Installation.model_validate_json(self.selection.read_bytes())

    def require_ready(self) -> None:
        if self.pending.exists():
            raise ValueError("运行环境切换尚未完成，请重试应用更新；数据保留")

    def status(self) -> DaemonStatus:
        return inspect_daemon(open_project(self.root, resolve_adapter=False))

    def activity(self) -> ApplicationActivity:
        status = self.status()
        if status.state in ("stopped", "stale"):
            return ApplicationActivity()
        if status.record is None:
            raise ValueError("无法确认后台工作状态")
        with httpx2.Client(timeout=3, trust_env=False) as client:
            response = client.get(
                status.record.base_url + "/api/v1/application-activity"
            )
            response.raise_for_status()
            return ApplicationActivity.model_validate(response.json())

    def stop_if_idle(self) -> bool:
        with self.lock:
            _ = stop_project(
                open_project(self.root, resolve_adapter=False), only_if_idle=True
            )
            return self.status().state == "stopped"

    def prepare_update(self, delivery: Path) -> Installation:
        python, bundle = prepare_home(delivery, self.installation().software_home)
        candidate = self.qualify(python, bundle / "gui")
        with self.lock:
            _write(self.candidate, candidate.model_dump_json(indent=2))
        return candidate

    def prepared_update(self) -> Installation | None:
        if self.pending.is_file():
            return Installation.model_validate_json(self.pending.read_bytes())
        if not self.candidate.is_file():
            return None
        candidate = Installation.model_validate_json(self.candidate.read_bytes())
        return candidate if candidate != self.installation() else None

    def qualify(
        self,
        python: Path,
        static_dir: Path | None,
        *,
        composition: str | None = None,
        software_home: Path | None = None,
    ) -> Installation:
        composition = composition or (self.root / "scopecat.toml").read_text()
        descriptor, name = tempfile.mkstemp(prefix=".candidate-", dir=self.root)
        manifest = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(composition)
            result = runtime_command(
                python,
                {
                    "action": "probe",
                    "root": str(self.root),
                    "manifest": str(manifest),
                    "static_dir": str(static_dir) if static_dir else None,
                },
            )
        finally:
            manifest.unlink(missing_ok=True)
        return Installation(
            software_home=(
                software_home.resolve()
                if software_home is not None
                else self.installation().software_home
            ),
            python=python.absolute(),
            static_dir=Path(cast("str", result["static_dir"])),
            environment=cast("dict[str, str]", result["environment"]),
            settings_identity=cast("str | None", result["settings_identity"]),
            adapter_identity=cast("str | None", result["adapter_identity"]),
            drivers=QualifiedDrivers.model_validate_json(json.dumps(result["drivers"]))
            if result["drivers"] is not None
            else None,
            composition=composition,
        )

    def configure(
        self,
        *,
        python: Path | None = None,
        static_dir: Path | None = None,
        adapter: AdapterReference | None = None,
        software_home: Path | None = None,
    ) -> Installation:
        """Prepare an empty application; never scaffold or load author code."""
        self.home.mkdir(parents=True, exist_ok=True)
        with self.lock:
            if self.selection.exists():
                if (
                    software_home is not None
                    and self.installation().software_home != software_home.resolve()
                ):
                    raise ValueError(
                        "已有应用使用不同的程序目录；请选择新的数据目录安装"
                    )
                return self.installation()
            manifest = self.root / "scopecat.toml"
            declaration = application_declaration(adapter)
            if manifest.exists() and manifest.read_text() != declaration:
                raise ValueError("已有应用声明与本次安装不符；原文件保留")
            if not manifest.exists():
                _write(manifest, declaration)
            selected = self.qualify(
                python or Path(sys.executable),
                static_dir,
                software_home=software_home or self.home / "software",
            )
            _write(self.selection, selected.model_dump_json(indent=2))
            return selected

    def start(self) -> DaemonEndpointRecord:
        with self.lock:
            self.require_ready()
            selected = self.installation()
            runtime_command(
                selected.python,
                {
                    "action": "start",
                    "root": str(self.root),
                    **selected.model_dump(
                        mode="json",
                        exclude={"python", "drivers", "composition", "software_home"},
                    ),
                },
            )
            status = self.status()
            if status.state != "running" or status.record is None:
                raise ValueError(f"应用尚未就绪：{status.detail or status.state}")
            from .cli import check_served_gui

            check_served_gui(self.root, selected.static_dir)
            return status.record

    def stop(self) -> None:
        """Stop the exact recorded process even when its interpreter is unavailable."""
        with self.lock:
            stop_project(open_project(self.root, resolve_adapter=False))
            if self.status().state != "stopped":
                raise ValueError("应用尚未停止，运行归属与数据保留；请查看诊断")

    def _require_stopped(self) -> None:
        status = self.status()
        if status.state == "stale" and status.record is not None:
            # Caller holds the data lock: a verified killed process can be
            # reconciled, while a live or ambiguous owner must remain untouched.
            stop_project(open_project(self.root, resolve_adapter=False))
            status = self.status()
        if status.state != "stopped":
            raise ValueError(f"请先停止应用再切换环境：{status.detail or status.state}")

    def select(self, candidate: Installation) -> None:
        """Commit qualified software and source interpreters under one start fence."""
        with self.lock, ExitStack() as locks:
            binding = open_project(self.root, resolve_adapter=False).runtime_binding
            try:
                for path in (
                    binding.deployment_root / "deployment.lock",
                    binding.data_root / "daemon.lock",
                ):
                    path.parent.mkdir(parents=True, exist_ok=True)
                    locks.enter_context(FileLock(path, timeout=0))
            except Timeout as error:
                raise ValueError(
                    "应用仍在运行或更新中；候选环境保留，可停止后重试"
                ) from error
            self._require_stopped()
            qualified = self.qualify(
                candidate.python,
                candidate.static_dir,
                composition=candidate.composition,
                software_home=candidate.software_home,
            )
            if qualified != candidate:
                raise ValueError("候选环境在准备后改变；请重新准备更新")
            if (
                self.pending.exists()
                and Installation.model_validate_json(self.pending.read_bytes())
                != candidate
            ):
                raise ValueError("请先重试上次尚未完成的环境切换")
            _write(self.pending, candidate.model_dump_json(indent=2))
            _write(self.root / "scopecat.toml", candidate.composition)
            _write(self.selection, candidate.model_dump_json(indent=2))
            self.pending.unlink()

    def register_source(self, workspace: Path, *, python: Path | None = None) -> str:
        with self.lock:
            self.require_ready()
            selected = self.installation()
            result = runtime_command(
                selected.python,
                {
                    "action": "register_source",
                    "root": str(self.root),
                    "workspace": str(workspace.resolve()),
                    "author_python": str(python.absolute()) if python else None,
                    **selected.model_dump(
                        mode="json",
                        exclude={"python", "drivers", "composition", "software_home"},
                    ),
                },
            )
            return cast("str", result["source_id"])

    def select_source_environment(self, workspace: Path, python: Path) -> None:
        """Publish an explicitly prepared execution environment without restarting."""
        from scopecat_server.author_environment import capture

        with self.lock:
            self.require_ready()
            identity = self.source(workspace)
            capture(workspace, python, owner=self.root)
            path = author_bindings_path(self.root)
            registry = LocalAuthorWorkspaces.model_validate_json(path.read_bytes())
            items = tuple(
                item.model_copy(
                    update={
                        "python": python,
                        "retained_pythons": tuple(
                            dict.fromkeys((item.python, *item.retained_pythons))
                        ),
                    }
                )
                if item.id == identity and item.python != python
                else item
                for item in registry.items
            )
            _write(
                path,
                registry.model_copy(update={"items": items}).model_dump_json(indent=2),
            )

    def source(self, workspace: Path) -> str:
        """Opening registered code joins this home; it cannot create another owner."""
        self.require_ready()
        project = open_project(workspace, resolve_adapter=False)
        owner = open_project(self.root, resolve_adapter=False).runtime_binding
        binding = project.runtime_binding
        if (binding.data_root, binding.deployment_root) != (
            owner.data_root,
            owner.deployment_root,
        ):
            raise ValueError("作者目录未绑定此应用；请先登记源码目录")
        return author_workspace_id(project.root)
