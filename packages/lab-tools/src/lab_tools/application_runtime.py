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
from scopecat.project import open_project
from scopecat_server.lifecycle import DaemonStatus, inspect_daemon, stop_project

from .bundle import MANIFEST, file_hash, installed_bundle, managed_path, read_bundle


class Installation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    python: Path
    static_dir: Path
    delivery_root: Path | None = None
    delivery_manifest_sha256: str | None = None
    environment: dict[str, str]
    settings_identity: str | None = None
    adapter_identity: str | None = None
    composition: str


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


def write_state(path: Path, content: str) -> None:
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
    """The data home records the application runtime and registered sources.

    There is no service registry, preferred service, or per-source endpoint.
    Preparation may fail without changing the selected installation. Activation
    shares the daemon's ownership locks and fences starts until bindings commit.
    """

    def __init__(self, home: Path):
        self.home = home.resolve()
        self.root = managed_path(self.home, self.home / "runtime")
        self.selection = managed_path(self.home, self.home / "installation.json")
        self.pending = managed_path(self.home, self.home / "installation-pending.json")
        self.lock = FileLock(self.home / "application.lock", timeout=30)

    def installation(self) -> Installation:
        if not self.selection.is_file():
            raise ValueError("应用尚未准备就绪，请重新打开 Scopecat 或在启动页面重试")
        return Installation.model_validate_json(self.selection.read_bytes())

    def require_ready(self) -> None:
        if (self.home / "data-reset.json").exists():
            raise ValueError("数据删除尚未完成；请在启动页面确认继续，不能直接启动")
        if self.pending.exists():
            raise ValueError("应用运行信息登记尚未完成，请在启动页面重试；数据保留")

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

    def qualify(
        self,
        python: Path,
        static_dir: Path | None,
        *,
        composition: str | None = None,
        delivery_root: Path | None = None,
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
        environment = cast("dict[str, str]", result["environment"])
        receipt_root = installed_bundle(Path(environment["prefix"]))
        if delivery_root is None:
            delivery_root = receipt_root
        if delivery_root is not None:
            delivery_root = delivery_root.resolve()
            _ = read_bundle(delivery_root)
        return Installation(
            python=python.absolute(),
            static_dir=Path(cast("str", result["static_dir"])),
            delivery_root=delivery_root,
            delivery_manifest_sha256=file_hash(delivery_root / MANIFEST)
            if delivery_root
            else None,
            environment=environment,
            settings_identity=cast("str | None", result["settings_identity"]),
            adapter_identity=cast("str | None", result["adapter_identity"]),
            composition=composition,
        )

    def configure(
        self,
        *,
        python: Path | None = None,
        static_dir: Path | None = None,
        delivery_root: Path | None = None,
    ) -> Installation:
        """Prepare an empty application; never scaffold or load author code."""
        self.home.mkdir(parents=True, exist_ok=True)
        with self.lock:
            if self.selection.exists():
                return self.installation()
            manifest = self.root / "scopecat.toml"
            declaration = "[lab]\n[authors]\ndependencies = []\n"
            if manifest.exists() and manifest.read_text() != declaration:
                raise ValueError("已有应用声明与本次安装不符；原文件保留")
            if not manifest.exists():
                write_state(manifest, declaration)
            selected = self.qualify(
                python or Path(sys.executable),
                static_dir,
                delivery_root=delivery_root,
            )
            write_state(self.selection, selected.model_dump_json(indent=2))
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
                        exclude={
                            "python",
                            "composition",
                            "delivery_root",
                            "delivery_manifest_sha256",
                        },
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
            raise ValueError(f"请先退出正在运行的应用：{status.detail or status.state}")

    def select(self, candidate: Installation) -> None:
        """Record a verified runtime under the same locks that fence startup."""
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
                    "应用仍在运行或更新中，请退出后重试；数据保留"
                ) from error
            self._require_stopped()
            qualified = self.qualify(
                candidate.python,
                candidate.static_dir,
                composition=candidate.composition,
                delivery_root=candidate.delivery_root,
            )
            if qualified != candidate:
                raise ValueError("应用文件在检查后改变，请重新打开 Scopecat 后重试")
            # A fully verified current package can supersede an interrupted
            # registration. The journal fences starts; it does not pin an old
            # package that may no longer be installed.
            write_state(self.pending, candidate.model_dump_json(indent=2))
            write_state(self.root / "scopecat.toml", candidate.composition)
            write_state(self.selection, candidate.model_dump_json(indent=2))
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
                        exclude={
                            "python",
                            "composition",
                            "delivery_root",
                            "delivery_manifest_sha256",
                        },
                    ),
                },
            )
            return cast("str", result["source_id"])

    def select_source_environment(self, workspace: Path, python: Path) -> None:
        """Publish an explicitly prepared execution environment without restarting."""
        from scopecat_server.author_environment import capture

        workspace = workspace.resolve()
        # Preserve venv executable symlinks; resolving them selects the base Python.
        python = python.absolute()
        with self.lock:
            self.require_ready()
            identity = self.source(workspace)
            capture(workspace, python)
            path = author_bindings_path(self.root)
            with FileLock(path.parent / "author-workspaces.lock", timeout=30):
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
                write_state(
                    path,
                    registry.model_copy(update={"items": items}).model_dump_json(
                        indent=2
                    ),
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
