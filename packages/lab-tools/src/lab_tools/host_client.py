"""Discover one authenticated, loopback-only application host per installation."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path
from threading import Thread
from typing import cast
from urllib.parse import urlsplit
from uuid import uuid4

import httpx2
import psutil
from filelock import FileLock
from pydantic import BaseModel, Field

from .bundle import file_hash
from .host_operations import Command, Operation, Workspace
from .sandboxes import sandbox_key


class HostRecord(BaseModel):
    protocol: int = 1
    instance: str
    pid: int
    process_time: float
    url: str
    token: str = Field(repr=False)
    runtime: str
    python: str


class HostState(BaseModel):
    home: str
    version: str
    topics: dict[str, str]
    workspaces: list[Workspace]
    operations: list[Operation]


def runtime_key(source: Path | None) -> str:
    package = Path(__file__).parent
    digest = hashlib.sha256()
    digest.update(f"{sys.executable}\n{source}\n{sandbox_key(source)}".encode())
    for path in sorted(package.rglob("*")):
        if path.is_file() and path.suffix in (".py", ".html", ".js", ".css"):
            digest.update(file_hash(path).encode())
    return digest.hexdigest()


def process_alive(record: HostRecord) -> bool:
    try:
        process = psutil.Process(record.pid)
        return (
            abs(process.create_time() - record.process_time) < 0.01
            and process.status() != psutil.STATUS_ZOMBIE
        )
    except psutil.NoSuchProcess:
        return False


class HostClient:
    def __init__(self, record: HostRecord):
        address = urlsplit(record.url)
        if (
            address.scheme != "http"
            or address.hostname != "127.0.0.1"
            or not address.port
        ):
            raise ValueError("本机管理服务地址无效")
        self.record = record

    def request(
        self, method: str, path: str, *, body: dict[str, object] | None = None
    ) -> object:
        with httpx2.Client(
            timeout=30,
            trust_env=False,
            headers={"Authorization": f"Bearer {self.record.token}"},
        ) as client:
            response = client.request(method, self.record.url + path, json=body)
        if response.is_error:
            raise ValueError(response.text)
        return cast("object", response.json())

    def state(self) -> HostState:
        return HostState.model_validate(self.request("GET", "/api/state"))

    def submit(self, command: Command) -> Operation:
        return Operation.model_validate(
            self.request("POST", "/api/operations", body=command.model_dump())
        )

    def wait(self, operation: Operation) -> Operation:
        while operation.status in ("starting", "running"):
            time.sleep(0.5)
            operation = Operation.model_validate(
                self.request("GET", f"/api/operations/{operation.command.id}")
            )
        if operation.status != "succeeded":
            raise ValueError(
                f"{operation.detail}\n操作编号: {operation.command.id}；"
                "日志保留在管理页面。"
            )
        return operation

    def shutdown(self) -> None:
        self.request("POST", "/api/shutdown", body={})
        deadline = time.monotonic() + 30
        while process_alive(self.record) and time.monotonic() < deadline:
            time.sleep(0.1)
        if process_alive(self.record):
            raise ValueError("管理服务尚未退出，请稍后重试；原记录与日志保留")


def ensure_host(home: Path, source: Path | None) -> HostClient:
    home = home.resolve()
    source = source.resolve() if source is not None else None
    directory = home / "host"
    directory.mkdir(parents=True, exist_ok=True)
    record_path = directory / "endpoint.json"
    expected = runtime_key(source)
    with FileLock(directory / "launch.lock", timeout=45):
        if record_path.exists():
            record = HostRecord.model_validate_json(
                record_path.read_text(encoding="utf-8")
            )
            if process_alive(record):
                client = HostClient(record)
                health = client.request("GET", "/api/identity")
                if health != {"instance": record.instance, "protocol": 1}:
                    raise ValueError("本机服务身份不匹配；保留记录并检查 host 日志")
                if record.runtime == expected:
                    return client
                client.shutdown()
        # Windows venv Python may launch a separate interpreter process. Bind
        # startup to a launch identity, not Popen's redirector PID.
        instance = uuid4().hex
        args = [
            sys.executable,
            "-m",
            "lab_tools.app_host",
            "--home",
            str(home),
            "--instance",
            instance,
        ]
        if source is not None:
            args.extend(("--source", str(source)))
        with (directory / "host.log").open("ab") as log:
            process = subprocess.Popen(  # noqa: S603 - fixed local module
                args,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                env=dict(os.environ, PYTHONUTF8="1"),
                start_new_session=sys.platform != "win32",
                creationflags=(
                    subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                )
                if sys.platform == "win32"
                else 0,
            )
        Thread(target=process.wait, daemon=True).start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise ValueError(f"管理服务启动失败，请查看 {directory / 'host.log'}")
            if record_path.exists():
                record = HostRecord.model_validate_json(
                    record_path.read_text(encoding="utf-8")
                )
                if record.instance == instance and record.runtime == expected:
                    client = HostClient(record)
                    try:
                        if client.request("GET", "/api/identity") == {
                            "instance": record.instance,
                            "protocol": 1,
                        }:
                            return client
                    except httpx2.TransportError:
                        pass
            time.sleep(0.1)
        raise ValueError(
            f"管理服务启动仍未完成；稍后重试。日志: {directory / 'host.log'}"
        )
