"""教学入口：连接一个本机管理服务，由它拥有练习生命周期。"""

from __future__ import annotations

import argparse
import json
import webbrowser
from pathlib import Path
from typing import Protocol, cast

import httpx2

from lab_teaching.lessons import TOPICS

from .host_client import HostClient, HostRecord, ensure_host, process_alive
from .host_operations import Command


class Arguments(Protocol):
    topic: str | None
    home: Path
    source: Path | None
    reset: bool
    stop: bool
    no_editor: bool
    verify: bool
    clean: bool
    shutdown: bool
    status: bool


def main(argv: list[str] | None = None) -> None:
    from .bundle import configure_console

    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("topic", choices=TOPICS, nargs="?")
    parser.add_argument("--home", type=Path, default=Path.home() / "Scopecat-Lab")
    parser.add_argument("--source", type=Path)
    for flag in ("reset", "stop", "no-editor", "verify", "clean", "shutdown", "status"):
        parser.add_argument(f"--{flag}", action="store_true")
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    try:
        if args.shutdown:
            path = args.home.resolve() / "host/endpoint.json"
            if path.exists():
                record = HostRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
                if process_alive(record):
                    HostClient(record).shutdown()
            print("管理服务已请求退出；练习服务不会被停止。")
            return
        client = ensure_host(args.home, args.source)
        state = client.state()
        if args.status:
            print(state.model_dump_json(indent=2))
            return
        if args.topic is None or args.clean:
            if args.no_editor:
                print(f"本机管理服务: {client.record.url}")
                print(
                    json.dumps(
                        [item.model_dump() for item in state.workspaces],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                webbrowser.open(f"{client.record.url}/#token={client.record.token}")
                print("已打开 Scopecat 教学管理页面。关闭页面不会停止后台操作。")
            return
        if args.stop:
            current = next(
                (
                    item
                    for item in state.workspaces
                    if item.topic == args.topic and item.current and item.active_version
                ),
                None,
            )
            if current is None:
                raise ValueError("此版本没有该专题的练习服务")
            command = Command(action="stop", workspace=current.id)
        else:
            command = Command(
                action="verify" if args.verify else "open",
                topic=args.topic,
                reset=args.reset,
            )
        print(f"操作编号: {command.id}；关闭窗口后仍可在管理页面查看进度。", flush=True)
        operation = client.wait(client.submit(command))
        print(f"已完成: {args.topic}；操作编号 {operation.command.id}")
        if operation.workspace is not None and command.action == "open":
            workspace = next(
                item
                for item in client.state().workspaces
                if item.id == operation.workspace
            )
            notebook = Path(workspace.root) / "notebooks" / f"{workspace.topic}.ipynb"
            print(f"项目: {workspace.root}\nNotebook: {notebook}")
            if not args.no_editor:
                print(client.request("POST", f"/api/editor/{workspace.id}", body={}))
    except (ValueError, OSError, httpx2.HTTPError) as error:
        parser.exit(2, f"{error}\n目录与日志保留，可从管理页面检查后重试。\n")


if __name__ == "__main__":
    main()
