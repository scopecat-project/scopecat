"""Explicit local deployments; not the daemon's scientific workspace registry."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Literal, cast
from uuid import uuid4

import httpx2
from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from scopecat.project import open_project
from scopecat_server.lifecycle import inspect_daemon


class Service(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    name: str = Field(min_length=1, max_length=200)
    root: str
    python: str
    static_dir: str
    environment: dict[str, str]
    settings_identity: str | None = None
    adapter_identity: str | None = None


class ServiceView(BaseModel):
    service: Service
    state: Literal["running", "stopped", "stale", "degraded", "unavailable"]
    url: str | None = None
    detail: str = ""


def _environment() -> dict[str, str]:
    result = dict(os.environ, PYTHONUTF8="1")
    for name in ("SCOPECAT_DAEMON_URL", "PYTHONHOME", "PYTHONPATH"):
        result.pop(name, None)
    return result


def _run(python: str, request: dict[str, object]) -> dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="scopecat-service-") as directory:
        output = Path(directory) / "result.json"
        try:
            subprocess.run(  # noqa: S603 - trusted CLI interpreter and fixed source
                [
                    python,
                    str(Path(__file__).with_name("service_runtime.py")),
                    json.dumps(request),
                    str(output),
                ],
                check=True,
                env=_environment(),
                # Service startup follows the existing progress/cancellation contract.
                timeout=30 if request["action"] == "probe" else None,
            )
        except subprocess.CalledProcessError as error:
            failure = (
                cast(
                    "dict[str, object]", json.loads(output.read_text(encoding="utf-8"))
                )
                if output.exists()
                else {}
            )
            detail = failure.get("error")
            raise ValueError(
                detail
                if isinstance(detail, str)
                else f"实验服务操作失败（退出代码 {error.returncode}）；请查看操作日志"
            ) from error
        return cast("dict[str, object]", json.loads(output.read_text(encoding="utf-8")))


class Services:
    def __init__(self, home: Path):
        directory = home / "host"
        directory.mkdir(parents=True, exist_ok=True)
        self.database = directory / "services.sqlite"
        self.lock = FileLock(directory / "services.lock", timeout=30)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS services "
                "(id TEXT PRIMARY KEY, root TEXT UNIQUE NOT NULL, "
                "payload TEXT NOT NULL)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS preference "
                "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
                "service TEXT NOT NULL)"
            )

    def preferred(self) -> Service | None:
        """Select the saved deployment, or a sole deployment before first choice."""
        with closing(sqlite3.connect(self.database)) as db:
            row = cast(
                "tuple[str] | None",
                db.execute(
                    "SELECT service FROM preference WHERE singleton=1"
                ).fetchone(),
            )
        records = self.list()
        if row is not None:
            # Retain a removed ID as a tombstone: never silently select a replacement.
            return next((item for item in records if item.id == row[0]), None)
        return records[0] if len(records) == 1 else None

    def remember(self, identity: str) -> None:
        """Remember only a still-registered deployment after successful startup."""
        with self.lock, closing(sqlite3.connect(self.database)) as db, db:
            self.get(identity)
            db.execute(
                "INSERT INTO preference VALUES (1, ?) "
                "ON CONFLICT(singleton) DO UPDATE SET service=excluded.service",
                (identity,),
            )

    def list(self) -> list[Service]:
        with closing(sqlite3.connect(self.database)) as db:
            rows = cast(
                "list[tuple[str]]",
                db.execute("SELECT payload FROM services ORDER BY rowid").fetchall(),
            )
        return [Service.model_validate_json(row[0]) for row in rows]

    def get(self, identity: str) -> Service:
        selected = next((item for item in self.list() if item.id == identity), None)
        if selected is None:
            raise ValueError("未找到已登记的实验服务；请从本机 CLI 登记项目")
        return selected

    def register(
        self, root: Path, python: Path, *, name: str, static_dir: Path | None = None
    ) -> Service:
        root = root.resolve()
        # Preserve a venv interpreter symlink, whose parent selects the environment.
        python = python.absolute()
        if not python.is_file():
            raise ValueError(f"实验环境解释器不存在: {python}")
        result = _run(
            str(python),
            {
                "action": "probe",
                "root": str(root),
                "static_dir": str(static_dir.resolve()) if static_dir else None,
            },
        )
        root = Path(cast("str", result["root"]))
        with self.lock, closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = cast(
                "tuple[str] | None",
                db.execute(
                    "SELECT payload FROM services WHERE root=?", (str(root),)
                ).fetchone(),
            )
            existing = Service.model_validate_json(row[0]) if row else None
            service = Service(
                id=existing.id if existing else uuid4().hex,
                name=name.strip(),
                root=str(root),
                python=str(python),
                static_dir=cast("str", result["static_dir"]),
                environment=cast("dict[str, str]", result["environment"]),
                settings_identity=cast("str | None", result["settings_identity"]),
                adapter_identity=cast("str | None", result["adapter_identity"]),
            )
            if existing and existing != service:
                from .host_store import Operations

                pending = any(
                    item.command.service == existing.id
                    and item.status in ("starting", "running")
                    for item in Operations(self.database.parent.parent).list()
                )
                if pending or inspect_daemon(
                    open_project(root, resolve_adapter=False)
                ).state in (
                    "running",
                    "degraded",
                ):
                    raise ValueError(
                        "请先完成管理操作并显式停止原实验服务，再变更登记环境"
                    )
            self._save(db, service)
        return service

    @staticmethod
    def _save(db: sqlite3.Connection, service: Service) -> None:
        db.execute(
            "INSERT INTO services VALUES (?, ?, ?) "
            "ON CONFLICT(root) DO UPDATE SET payload=excluded.payload",
            (service.id, service.root, service.model_dump_json()),
        )

    def recheck(self, identity: str, *, operation_id: str) -> Service:
        """Revalidate an existing stopped deployment without changing its paths."""
        from .host_store import Operations

        with self.lock:
            service = self.get(identity)
            operations = Operations(self.database.parent.parent)
            operations.reconcile()
            if any(
                item.command.id != operation_id
                and item.status in ("starting", "running")
                for item in operations.list()
            ):
                raise ValueError("还有管理操作未完成，不能复检登记环境")
            self._require_stopped(service)
            print(
                "复检前登记环境: "
                + json.dumps(service.environment, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
            result = _run(
                service.python,
                {
                    "action": "probe",
                    "root": service.root,
                    "static_dir": service.static_dir,
                },
            )
            if (
                result["root"] != service.root
                or result["static_dir"] != service.static_dir
            ):
                raise ValueError("登记路径已改变；请从本机 CLI 重新登记，原登记保留")
            updated = service.model_copy(
                update={
                    "environment": cast("dict[str, str]", result["environment"]),
                    "adapter_identity": cast("str | None", result["adapter_identity"]),
                    "settings_identity": cast(
                        "str | None", result["settings_identity"]
                    ),
                }
            )
            # A trusted local CLI could start the daemon while the probe runs.
            self._require_stopped(service)
            with closing(sqlite3.connect(self.database)) as db, db:
                self._save(db, updated)
            print(
                "复检后登记环境: "
                + json.dumps(updated.environment, ensure_ascii=False, sort_keys=True),
                flush=True,
            )
            return updated

    @staticmethod
    def _require_stopped(service: Service) -> None:
        if (
            inspect_daemon(open_project(service.root, resolve_adapter=False)).state
            != "stopped"
        ):
            raise ValueError("请先确认实验服务已停止，再复检环境；原登记保留")

    def views(self) -> list[ServiceView]:
        result: list[ServiceView] = []
        for service in self.list():
            try:
                status = inspect_daemon(
                    open_project(service.root, resolve_adapter=False)
                )
                result.append(
                    ServiceView(
                        service=service,
                        state=status.state,
                        url=status.record.base_url
                        if status.state == "running" and status.record
                        else None,
                        detail=status.detail or "",
                    )
                )
            except (OSError, ValueError) as error:
                result.append(
                    ServiceView(service=service, state="unavailable", detail=str(error))
                )
        return result

    def start(self, identity: str) -> None:
        service = self.get(identity)
        _run(
            service.python,
            {
                "action": "start",
                "root": service.root,
                "static_dir": service.static_dir,
                "environment": service.environment,
                "settings_identity": service.settings_identity,
                "adapter_identity": service.adapter_identity,
            },
        )

        status = inspect_daemon(open_project(service.root, resolve_adapter=False))
        if status.record is None or status.state != "running":
            raise ValueError("实验服务尚未就绪，请查看操作日志")
        with httpx2.Client(timeout=10, trust_env=False) as client:
            response = client.get(status.record.base_url + "/")
        if (
            response.status_code != 200
            or response.content
            != (Path(service.static_dir) / "index.html").read_bytes()
        ):
            raise ValueError(
                "运行中的服务没有提供登记的 GUI；请先显式停止原服务，再重新打开"
            )

    def stop(self, identity: str) -> None:
        with self.lock:
            service = self.get(identity)
            _run(
                service.python,
                {
                    "action": "stop",
                    "root": service.root,
                    "static_dir": None,
                    "environment": service.environment,
                },
            )
            if (
                inspect_daemon(open_project(service.root, resolve_adapter=False)).state
                != "stopped"
            ):
                raise ValueError("实验服务尚未停止；登记与数据保留，请查看操作日志")

    def remove(self, identity: str, *, operation_id: str) -> None:
        from .host_store import Operations

        with self.lock, closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            service = self.get(identity)
            operations = Operations(self.database.parent.parent)
            operations.reconcile()
            if any(
                item.command.id != operation_id
                and item.command.service == identity
                and item.status in ("starting", "running")
                for item in operations.list()
            ):
                raise ValueError("实验服务还有管理操作未完成，不能移除登记")
            if (
                inspect_daemon(open_project(service.root, resolve_adapter=False)).state
                != "stopped"
            ):
                raise ValueError("请先停止实验服务，再移除登记；项目和数据不会删除")
            db.execute("DELETE FROM services WHERE id=?", (identity,))
