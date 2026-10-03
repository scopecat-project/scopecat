"""Direct application entry; author folders never select or create services."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Protocol, cast
from urllib.parse import urlencode

from scopecat.installed_adapter import AdapterReference

from .application_runtime import ApplicationRuntime, application_declaration
from .bundle import configure_console


class Arguments(Protocol):
    action: str
    workspace: Path | None
    python: Path | None
    static_dir: Path | None
    home: Path
    source: Path | None
    no_browser: bool
    distribution: str | None
    manifest: str | None


def _author_environment(runtime: ApplicationRuntime, args: Arguments) -> None:
    from .author_environment import (
        create_client_environment,
        prepare_execution_environment,
    )

    assert args.workspace is not None
    if args.action == "prepare-author-environment":
        python = prepare_execution_environment(runtime, args.workspace)
        runtime.select_source_environment(args.workspace, python)
        print("后台依赖已准备；重新预览使用新环境，已有任务保留原环境。")
    else:
        print(
            create_client_environment(
                runtime,
                args.workspace,
                rebuild=args.action == "rebuild-author-environment",
            )
        )


def main(argv: list[str] | None = None) -> None:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action",
        choices=(
            "status",
            "configure",
            "update",
            "register-source",
            "select-source-environment",
            "prepare-author-environment",
            "create-author-environment",
            "rebuild-author-environment",
            "start",
            "stop",
            "open",
            "desktop",
        ),
        default="status",
        help="Only open explicitly launches a browser; start remains headless",
    )
    parser.add_argument(
        "--workspace", type=Path, help="An author folder in this application"
    )
    parser.add_argument("--python", type=Path)
    parser.add_argument("--static-dir", type=Path)
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument(
        "--source", type=Path, help="Framework checkout for development GUI assets"
    )
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--distribution", help="Installed capability distribution for initial setup"
    )
    parser.add_argument("--manifest", help="Distribution-owned capability manifest")
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    if bool(args.distribution) != bool(args.manifest):
        parser.error("--distribution 与 --manifest 需一起填写")
    if (
        args.action
        in (
            "register-source",
            "select-source-environment",
            "prepare-author-environment",
            "create-author-environment",
            "rebuild-author-environment",
        )
        and args.workspace is None
    ):
        parser.error("登记源码需要 --workspace")
    if args.action == "select-source-environment" and args.python is None:
        parser.error("选择执行环境需要 --python 指向已有解释器")
    runtime = ApplicationRuntime(args.home)
    try:
        if args.action in ("configure", "update"):
            adapter = (
                AdapterReference(args.distribution, args.manifest)
                if args.distribution is not None and args.manifest is not None
                else None
            )
            selected = runtime.configure(
                python=args.python,
                static_dir=args.static_dir
                or (args.source / "apps/scopecat-ui/dist" if args.source else None),
                adapter=adapter,
            )
            if args.action == "update":
                selected = runtime.qualify(
                    args.python or Path(sys.executable),
                    args.static_dir,
                    composition=application_declaration(adapter) if adapter else None,
                )
                runtime.select(selected)
            print(selected.model_dump_json(indent=2))
        elif args.action == "register-source":
            assert args.workspace is not None
            print(runtime.register_source(args.workspace, python=args.python))
        elif args.action == "select-source-environment":
            assert args.workspace is not None and args.python is not None
            runtime.select_source_environment(args.workspace, args.python)
            print("执行环境已选择；重新预览使用新环境，已有任务保留原环境。")
        elif args.action in (
            "create-author-environment",
            "rebuild-author-environment",
            "prepare-author-environment",
        ):
            _author_environment(runtime, args)
        elif args.action == "status":
            if not runtime.selection.is_file():
                print(json.dumps({"state": "not-installed", "home": str(runtime.home)}))
                return
            status = runtime.status()
            print(
                json.dumps(
                    {
                        "state": status.state,
                        "detail": status.detail,
                        "url": status.record.base_url if status.record else None,
                        "home": str(runtime.home),
                        "installation": runtime.installation().model_dump(mode="json"),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.action == "stop":
            runtime.stop()
            print("应用已停止；数据和作者目录保留。")
        elif args.action == "desktop":
            from .desktop import run

            run(args.home.resolve(), args.source)
        else:
            identity = runtime.source(args.workspace) if args.workspace else None
            record = runtime.start()
            url = record.base_url
            if identity:
                url += "/?" + urlencode({"workspace": identity})
            if args.action == "open" and not args.no_browser:
                webbrowser.open(url)
            print(url)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.exit(2, f"{error}\n已有软件、数据和源码保留；没有自动打开浏览器。\n")


if __name__ == "__main__":
    main()
