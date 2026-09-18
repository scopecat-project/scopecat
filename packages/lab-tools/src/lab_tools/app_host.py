"""Local application host: one authenticated entry manages teaching workers."""

from __future__ import annotations

import argparse
import os
import secrets
import shutil
import socket
import subprocess
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from threading import Lock
from typing import Protocol, cast
from uuid import uuid4

import psutil
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from filelock import FileLock

from lab_teaching.lessons import TOPICS

from .bundle import configure_console
from .host_client import HostRecord, HostState, runtime_key
from .host_operations import (
    Command,
    Operation,
    Operations,
    launch,
    owned_workspace,
    workspaces,
)
from .sandboxes import sandbox_key


def application(
    home: Path, source: Path | None, record: HostRecord, shutdown: Callable[[], None]
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = Operations(home)
    key = sandbox_key(source)
    authority = record.url.removeprefix("http://")
    assets = Path(__file__).parent / "host_assets"
    admission = Lock()
    closing = False

    @app.middleware("http")
    async def boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.headers.get("host") != authority:
            return JSONResponse({"detail": "Invalid local host"}, status_code=403)
        origin = request.headers.get("origin")
        if origin is not None and origin != record.url:
            return JSONResponse(
                {"detail": "Cross-origin access denied"}, status_code=403
            )
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("authorization", ""), f"Bearer {record.token}"
        ):
            return JSONResponse(
                {"detail": "请从 Scopecat 安装入口打开管理页面"}, status_code=401
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ValueError)
    async def invalid(_request: Request, error: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(error)}, status_code=409)

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(assets / "index.html")

    @app.get("/app.js")
    def javascript() -> FileResponse:
        return FileResponse(assets / "app.js", media_type="text/javascript")

    @app.get("/app.css")
    def stylesheet() -> FileResponse:
        return FileResponse(assets / "app.css", media_type="text/css")

    @app.get("/api/identity")
    def identity() -> dict[str, object]:
        return {"instance": record.instance, "protocol": 1}

    @app.get("/api/state")
    def state() -> HostState:
        store.reconcile()
        return HostState(
            home=str(home),
            version=key,
            topics=TOPICS,
            workspaces=workspaces(home, key),
            operations=store.list(),
        )

    @app.post("/api/operations")
    def submit(command: Command) -> Operation:
        with admission:
            if closing:
                raise ValueError("管理服务正在退出，请重新打开安装入口")
            return launch(home, source, command)

    @app.get("/api/operations/{identity}")
    def operation(identity: str) -> Operation:
        store.reconcile()
        return store.get(identity)

    @app.get("/api/operations/{identity}/log")
    def operation_log(identity: str) -> dict[str, str]:
        item = store.get(identity)
        path = store.directory / f"{item.command.id}.log"
        if not path.exists():
            return {"text": "操作尚未开始"}
        with path.open("rb") as log:
            log.seek(max(0, path.stat().st_size - 65536))
            content = log.read().decode("utf-8", errors="replace")
        return {"text": content}

    @app.post("/api/editor/{identity}")
    def editor(identity: str) -> dict[str, str]:
        workspace = owned_workspace(home, key, identity)
        code = shutil.which("code")
        if code is None:
            return {
                "detail": f"请在 VS Code 中打开 {workspace.root}，"
                "并选择其中的 .venv 内核"
            }
        subprocess.run(  # noqa: S603 - verified managed paths, no shell
            [
                code,
                workspace.root,
                str(Path(workspace.root) / "notebooks" / f"{workspace.topic}.ipynb"),
            ],
            check=True,
            timeout=20,
        )
        return {"detail": "已在 VS Code 中打开练习，请选择该练习的 .venv 内核"}

    @app.post("/api/shutdown")
    def stop() -> dict[str, str]:
        nonlocal closing
        with admission:
            store.reconcile()
            if any(item.status in ("starting", "running") for item in store.list()):
                raise ValueError("管理操作尚未完成，不能退出或升级管理服务")
            closing = True
            shutdown()
        return {"detail": "管理服务正在退出；已有练习服务保持运行"}

    return app


class Arguments(Protocol):
    home: Path
    source: Path | None
    instance: str


def main() -> None:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--instance", default=uuid4().hex)
    args = cast("Arguments", cast("object", parser.parse_args()))
    home = args.home.resolve()
    directory = home / "host"
    directory.mkdir(parents=True, exist_ok=True)
    with FileLock(directory / "owner.lock", timeout=0), socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        record = HostRecord(
            instance=args.instance,
            pid=os.getpid(),
            process_time=psutil.Process().create_time(),
            url=f"http://127.0.0.1:{listener.getsockname()[1]}",
            token=secrets.token_urlsafe(32),
            runtime=runtime_key(args.source),
            python=sys.executable,
        )

        def shutdown() -> None:
            server.should_exit = True

        server = uvicorn.Server(
            uvicorn.Config(
                application(home, args.source, record, shutdown),
                log_level="warning",
                access_log=False,
            )
        )
        endpoint = directory / "endpoint.json"
        temporary = endpoint.with_suffix(".tmp")
        temporary.write_text(record.model_dump_json(), encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(endpoint)
        try:
            server.run(sockets=[listener])
        finally:
            endpoint.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
