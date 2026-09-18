"""Durable local teaching operations; workers own their installed environment."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from threading import Thread
from typing import Literal, cast
from uuid import uuid4

import psutil
from pydantic import BaseModel, ConfigDict, Field

from lab_teaching.lessons import TOPICS

from .cleanup import in_use, read_record, remove_old_sandbox
from .project import METADATA
from .sandboxes import sandbox_key, select_project


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=r"^[0-9a-f]{32}$")
    action: Literal["open", "verify", "stop", "delete"]
    topic: str | None = None
    workspace: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")
    reset: bool = False


class Operation(BaseModel):
    command: Command
    status: Literal["starting", "running", "succeeded", "failed", "interrupted"]
    created: float = Field(default_factory=time.time)
    pid: int | None = None
    process_time: float | None = None
    detail: str = ""
    workspace: str | None = None


class Workspace(BaseModel):
    id: str
    topic: str
    title: str
    root: str
    version: str
    current: bool
    active_version: bool
    in_use: bool
    deletable: bool


def workspaces(home: Path, key: str) -> list[Workspace]:
    base = home.resolve() / "sandboxes"
    if base.is_symlink():
        raise ValueError("沙盒目录不能指向外部目录")
    result: list[Workspace] = []
    for root in sorted(base.glob("*/*/*")):
        if (
            not root.is_dir()
            or root.parent.name not in TOPICS
            or len(root.name) != 32
            or any(c not in "0123456789abcdef" for c in root.name)
            or any(p.is_symlink() for p in (root, root.parent, root.parent.parent))
        ):
            continue
        try:
            if read_record(root / METADATA).get("kind") != "teaching":
                continue
            pointer = root.parent / "current.json"
            current = (
                pointer.exists() and read_record(pointer).get("generation") == root.name
            )
        except OSError, ValueError:
            continue
        active = root.parent.parent.name == key
        busy = in_use(root)
        result.append(
            Workspace(
                id=root.name,
                topic=root.parent.name,
                title=TOPICS[root.parent.name],
                root=str(root),
                version=root.parent.parent.name,
                current=current,
                active_version=active,
                in_use=busy,
                deletable=not (active and current) and not busy,
            )
        )
    return result


def owned_workspace(home: Path, key: str, identity: str) -> Workspace:
    matches = [item for item in workspaces(home, key) if item.id == identity]
    if len(matches) != 1:
        raise ValueError("未找到此受管理的教学练习；请刷新列表")
    return matches[0]


class Operations:
    def __init__(self, home: Path):
        self.directory = home.resolve() / "host"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.database = self.directory / "operations.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS operations "
                "(id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )

    def list(self) -> list[Operation]:
        with closing(sqlite3.connect(self.database)) as db:
            rows = cast(
                "list[tuple[str]]",
                db.execute(
                    "SELECT payload FROM operations ORDER BY rowid DESC LIMIT 100"
                ).fetchall(),
            )
        return [Operation.model_validate_json(row[0]) for row in rows]

    def get(self, identity: str) -> Operation:
        with closing(sqlite3.connect(self.database)) as db:
            row = cast(
                "tuple[str] | None",
                db.execute(
                    "SELECT payload FROM operations WHERE id=?", (identity,)
                ).fetchone(),
            )
        if row is None:
            raise ValueError("操作不存在")
        return Operation.model_validate_json(row[0])

    def save(self, operation: Operation) -> None:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute(
                "UPDATE operations SET payload=? WHERE id=?",
                (operation.model_dump_json(), operation.command.id),
            )

    def reconcile(self) -> None:
        for item in self.list():
            if item.status == "starting" and time.time() - item.created < 30:
                continue
            if item.status not in ("starting", "running"):
                continue
            if item.pid is not None and item.process_time is not None:
                try:
                    process = psutil.Process(item.pid)
                    if (
                        abs(process.create_time() - item.process_time) < 0.01
                        and process.status() != psutil.STATUS_ZOMBIE
                    ):
                        continue
                except psutil.NoSuchProcess:
                    pass
            # Re-read under the writer lock: a worker may have just completed.
            with closing(sqlite3.connect(self.database)) as db, db:
                db.execute("BEGIN IMMEDIATE")
                row = cast(
                    "tuple[str]",
                    db.execute(
                        "SELECT payload FROM operations WHERE id=?", (item.command.id,)
                    ).fetchone(),
                )
                latest = Operation.model_validate_json(row[0])
                if latest == item:
                    latest.status = "interrupted"
                    latest.detail = (
                        "操作进程已退出；目录与日志保留。"
                        "检查结果后再重试，不会自动重放。"
                    )
                    db.execute(
                        "UPDATE operations SET payload=? WHERE id=?",
                        (latest.model_dump_json(), item.command.id),
                    )

    def begin(self, command: Command) -> tuple[Operation, bool]:
        self.reconcile()
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            rows = cast(
                "list[tuple[str]]",
                db.execute("SELECT payload FROM operations").fetchall(),
            )
            operations = [Operation.model_validate_json(row[0]) for row in rows]
            existing = next(
                (item for item in operations if item.command.id == command.id), None
            )
            if existing is not None:
                if existing.command != command:
                    raise ValueError("同一操作编号不能用于不同请求")
                return existing, False
            if any(item.status in ("starting", "running") for item in operations):
                raise ValueError("另一项教学管理操作正在进行，请完成后再试")
            operation = Operation(command=command, status="starting")
            db.execute(
                "INSERT INTO operations VALUES (?, ?)",
                (command.id, operation.model_dump_json()),
            )
            return operation, True

    def claim(self, identity: str) -> Operation:
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = cast(
                "tuple[str]",
                db.execute(
                    "SELECT payload FROM operations WHERE id=?", (identity,)
                ).fetchone(),
            )
            operation = Operation.model_validate_json(row[0])
            if operation.status != "starting":
                raise ValueError("操作已被领取或结束")
            operation.status = "running"
            operation.pid = os.getpid()
            operation.process_time = psutil.Process().create_time()
            db.execute(
                "UPDATE operations SET payload=? WHERE id=?",
                (operation.model_dump_json(), identity),
            )
        return operation


def launch(home: Path, source: Path | None, command: Command) -> Operation:
    if command.action in ("open", "verify"):
        if command.topic not in TOPICS or command.workspace is not None:
            raise ValueError("请选择有效专题")
    elif command.workspace is None or command.reset or command.topic is not None:
        raise ValueError("请选择受管理练习的编号")
    operations = Operations(home)
    operation, created = operations.begin(command)
    if not created:
        return operation
    args = [sys.executable, "-m", "lab_tools.host_worker", str(home), command.id]
    if source is not None:
        args.extend(("--source", str(source)))
    try:
        with (operations.directory / f"{command.id}.log").open("ab") as log:
            child = subprocess.Popen(  # noqa: S603 - fixed worker entry, no user shell
                args,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=sys.platform != "win32",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32"
                else 0,
            )
        Thread(target=child.wait, daemon=True).start()
    except OSError as error:
        operation.status = "failed"
        operation.detail = str(error)
        operations.save(operation)
    return operation


def execute(home: Path, source: Path | None, command: Command) -> str:
    from .notebook import project_python
    from .sandboxes import run

    key = sandbox_key(source)
    if command.action in ("stop", "delete"):
        assert command.workspace is not None
        root = Path(owned_workspace(home, key, command.workspace).root)
        if command.action == "delete":
            remove_old_sandbox(home, key, root)
        else:
            run([str(project_python(root)), "-m", "lab_tools.cli", "stop", str(root)])
        return root.name
    assert command.topic is not None
    if command.reset:
        current = next(
            (
                item
                for item in workspaces(home, key)
                if item.topic == command.topic and item.current and item.active_version
            ),
            None,
        )
        if current is not None and current.in_use:
            raise ValueError("重置前请关闭 Notebook 内核，并在管理页面停止此练习的服务")
    root = select_project(home, command.topic, source=source, reset=command.reset)
    python = str(project_python(root))
    if command.action == "verify":
        args = [python, "-m", "lab_tools.verify_lesson", str(root), command.topic]
    else:
        args = [python, "-m", "lab_tools.cli", "start", str(root)]
    if source is not None:
        args.append("--api-only")
    run(args)
    return root.name
