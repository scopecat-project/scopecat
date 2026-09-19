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
            )
            if existing and existing != service:
                from .host_store import Operations

                pending = any(
                    item.command.service == existing.id
                    and item.status in ("starting", "running")
                    for item in Operations(self.database.parent.parent).list()
                )
                if pending or inspect_daemon(open_project(root)).state in (
                    "running",
                    "degraded",
                ):
                    raise ValueError(
                        "请先完成管理操作并显式停止原实验服务，再变更登记环境"
                    )
            db.execute(
                "INSERT INTO services VALUES (?, ?, ?) "
                "ON CONFLICT(root) DO UPDATE SET payload=excluded.payload",
                (service.id, service.root, service.model_dump_json()),
            )
        return service

    def views(self) -> list[ServiceView]:
        result: list[ServiceView] = []
        for service in self.list():
            try:
                status = inspect_daemon(open_project(service.root))
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
            },
        )

        status = inspect_daemon(open_project(service.root))
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
