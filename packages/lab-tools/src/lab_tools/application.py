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
from .development import prepare_capability


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
    bundle: Path | None
    package: Path | None


def main(argv: list[str] | None = None) -> None:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--action",
        choices=(
            "status",
            "configure",
            "update",
            "prepare-update",
            "prepare-capability",
            "apply-update",
            "register-source",
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
    parser.add_argument("--bundle", type=Path, help="Candidate delivery directory")
    parser.add_argument(
        "--package", type=Path, help="Editable capability package source"
    )
    parser.add_argument(
        "--distribution", help="Installed capability distribution for initial setup"
    )
    parser.add_argument("--manifest", help="Distribution-owned capability manifest")
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    if bool(args.distribution) != bool(args.manifest):
        parser.error("--distribution 与 --manifest 需一起填写")
    if args.action == "register-source" and args.workspace is None:
        parser.error("登记源码需要 --workspace")
    if args.action == "prepare-update" and args.bundle is None:
        parser.error("准备更新需要 --bundle")
    if args.action == "prepare-capability" and args.package is None:
        parser.error("开发能力快照需要 --package")
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
        elif args.action == "prepare-update":
            assert args.bundle is not None
            print(runtime.prepare_update(args.bundle).model_dump_json(indent=2))
        elif args.action == "prepare-capability":
            assert args.package is not None
            print(prepare_capability(runtime, args.package).model_dump_json(indent=2))
        elif args.action == "apply-update":
            candidate = runtime.prepared_update()
            if candidate is None:
                raise ValueError("请先准备更新，资格核验通过后再切换")
            runtime.select(candidate)
            print("已切换应用环境；请重新打开 Scopecat 并重启 Python 内核。")
        elif args.action == "register-source":
            assert args.workspace is not None
            print(runtime.register_source(args.workspace))
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
