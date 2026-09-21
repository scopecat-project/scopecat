"""Foreground Notebook entry for code bound to one registered laboratory."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol, cast

from .bundle import configure_console
from .notebook import kernel_command
from .services import Services


class Arguments(Protocol):
    workspace: Path
    home: Path
    no_browser: bool


def launch_notebook(workspace: Path, home: Path, *, no_browser: bool = False) -> int:
    workspace = workspace.resolve()
    home = home.resolve()
    store = Services(home)
    # A session gets its own kernelspec. A later launch must not redirect kernels
    # created by a still-running Jupyter server from an earlier environment.
    with tempfile.TemporaryDirectory(prefix="scopecat-notebook-") as directory:
        with store.lock:
            service, identity = store.for_workspace(workspace)
            command, env = kernel_command(
                workspace,
                python=service.python,
                source_path=False,
                kernel_home=Path(directory),
            )
            for name in ("PYTHONHOME", "PYTHONPATH", "SCOPECAT_DAEMON_URL"):
                env.pop(name, None)
            checked = subprocess.run(  # noqa: S603 - registered interpreter, fixed probe
                [
                    service.python,
                    "-I",
                    "-c",
                    (
                        "import importlib.util; raise SystemExit(not all("
                        "importlib.util.find_spec(name) is not None "
                        "for name in ('jupyterlab', 'ipykernel')))"
                    ),
                ],
                env=env,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if checked.returncode:
                raise ValueError(
                    "实验室环境无法加载 JupyterLab/ipykernel；请由维护者提供包含 "
                    "scopecat-lab-tools[notebook] 的交付。没有安装或回退到其他解释器。"
                    + checked.stderr.strip()
                )
            if no_browser:
                command.append("--no-browser")
            print(
                f"Notebook 作者目录: {workspace} ({identity})\n"
                f"实验室: {service.name}\n解释器: {service.python}\n"
                "仅打开编辑环境；实验服务请通过工作台显式启动。"
                "更新实验室前请先关闭此 Notebook 服务和全部内核。",
                flush=True,
            )
            process = subprocess.Popen(command, cwd=workspace, env=env)  # noqa: S603 - fixed Jupyter entry in registered interpreter
        try:
            return process.wait()
        except KeyboardInterrupt:
            # Jupyter receives the terminal interrupt too and owns its shutdown UI.
            return process.wait()


def main(argv: list[str] | None = None) -> None:
    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("workspace", type=Path, nargs="?", default=Path.cwd())
    parser.add_argument("--home", type=Path, default=Path.home() / "Scopecat-Lab")
    parser.add_argument("--no-browser", action="store_true")
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    try:
        status = launch_notebook(args.workspace, args.home, no_browser=args.no_browser)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(2, f"{error}\n")
    if status:
        parser.exit(status)


if __name__ == "__main__":
    main()
