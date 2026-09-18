"""维护者教学入口: 源码快速体验或构建固定交付。需要 Python 3.14 与 uv。"""

import argparse
import io
import os
import subprocess
import sys
from datetime import UTC
from pathlib import Path
from typing import Protocol, cast

ROOT = Path(__file__).resolve().parent


class Arguments(Protocol):
    mode: str
    home: Path
    install: bool


def run(command: list[str]) -> None:
    _ = subprocess.run(command, cwd=ROOT, check=True)  # noqa: S603 - explicit tool arguments


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    os.environ["PYTHONUTF8"] = "1"
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument(
        "mode", choices=("source", "release"), nargs="?", default="source"
    )
    _ = parser.add_argument("--home", type=Path, default=Path.home() / "Scopecat-Lab")
    _ = parser.add_argument(
        "--install", action="store_true", help="构建后在本机安装; 否则用于离线转移"
    )
    parsed, remaining = parser.parse_known_args()
    args = cast("Arguments", cast("object", parsed))
    # Explicitly use the checkout's environment, even from an activated old sandbox.
    os.environ.pop("VIRTUAL_ENV", None)
    os.environ["UV_PROJECT_ENVIRONMENT"] = (
        str(ROOT / "results/source-runtime")
        if args.mode == "source"
        else str(ROOT / ".venv")
    )
    print("准备维护运行环境...", flush=True)
    run(
        [
            "uv",
            "sync",
            "--locked",
            *(
                ["--only-group", "delivery", "--no-editable"]
                if args.mode == "source"
                else ["--group", "delivery"]
            ),
            "--inexact",
            "--reinstall-package",
            "scopecat-lab-tools",
            "--reinstall-package",
            "scopecat",
            "--reinstall-package",
            "scopecat-server",
            "--reinstall-package",
            "scopecat-lab-teaching",
        ]
    )
    if args.mode == "source":
        run(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-m",
                "lab_tools.sandbox",
                "--source",
                str(ROOT),
                "--home",
                str(args.home),
                *remaining,
            ]
        )
    else:
        if remaining:
            parser.error(f"release 不接受专题参数: {' '.join(remaining)}")
        from datetime import datetime
        from uuid import uuid4

        destination = (
            ROOT
            / "results/releases"
            / (
                datetime.now(UTC).strftime("%Y%m%d-%H%M%S-")
                + sys.platform
                + "-"
                + uuid4().hex[:8]
            )
        )
        print("构建固定交付 (需要干净源码和 Node/pnpm)...", flush=True)
        run(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-m",
                "lab_tools.delivery",
                "--release",
                str(destination),
            ]
        )
        print("空缓存离线验收完整课程与专题沙盒...", flush=True)
        run(
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                "scripts/verify_teaching_delivery.py",
                str(destination),
                str(ROOT / "results/validation" / destination.name),
            ]
        )
        if args.install:
            run(
                [
                    "uv",
                    "run",
                    "--no-sync",
                    "python",
                    str(destination / "install.py"),
                    "--home",
                    str(args.home),
                ]
            )
        print(f"已构建并验证交付: {destination}")


if __name__ == "__main__":
    try:
        main()
    except (OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"当前阶段失败, 未继续后续步骤: {error}") from error
