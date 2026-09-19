"""One durable operation ledger, independent of operation implementations."""

from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import cast

import psutil

from .host_models import Command, Operation


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
                raise ValueError("另一项管理操作正在进行，请完成后再试")
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
