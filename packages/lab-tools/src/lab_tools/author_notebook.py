"""Optional foreground Notebook entry for code bound to the application."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol, cast

from .application_runtime import ApplicationRuntime
from .bundle import configure_console
from .notebook import kernel_command, project_python


class Arguments(Protocol):
    workspace: Path | None
    home: Path
    no_browser: bool


def launch_notebook(
    workspace: Path | None, home: Path, *, no_browser: bool = False
) -> int:
    home = home.resolve()
    store = ApplicationRuntime(home)
    # A session gets its own kernelspec. A later launch must not redirect kernels
    # created by a still-running Jupyter server from an earlier environment.
    with tempfile.TemporaryDirectory(prefix="scopecat-notebook-") as directory:
        with store.lock:
            if workspace is None:
                from scopecat.author_workspaces import local_author_workspaces

                sources = local_author_workspaces(store.root)
                if len(sources) != 1:
                    raise ValueError(
                        "请登记作者目录；有多个目录时，"
                        "用 scopecat notebook 指定要打开的目录"
                    )
                workspace = sources[0].root
            workspace = workspace.resolve()
            identity = store.source(workspace)
            python = project_python(workspace)
            command, env = kernel_command(
                workspace,
                python=str(python),
                source_path=False,
                kernel_home=Path(directory),
            )
            for name in ("PYTHONHOME", "PYTHONPATH", "SCOPECAT_DAEMON_URL"):
                env.pop(name, None)
            checked = subprocess.run(  # noqa: S603 - registered interpreter, fixed probe
                [
                    str(python),
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
                    "本地环境无法加载 JupyterLab/ipykernel；可直接使用 VS Code，"
                    "或在此目录 .venv 中用 pip install jupyterlab 安装可选编辑器。"
                    + checked.stderr.strip()
                )
            if no_browser:
                command.append("--no-browser")
            print(
                f"Notebook 作者目录: {workspace} ({identity})\n"
                f"应用: {store.home}\n解释器: {python}\n"
                "仅打开编辑环境；打开 Scopecat 即可启动应用。"
                "切换运行环境前请先关闭此 Notebook 服务和全部内核。",
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
    parser.add_argument(
        "workspace",
        type=Path,
        nargs="?",
        help="作者目录；省略时使用应用的唯一作者目录",
    )
    parser.add_argument("--home", type=Path, default=Path.home() / "Scopecat-Lab")
    parser.add_argument("--no-browser", action="store_true")
    args = cast("Arguments", cast("object", parser.parse_args(argv)))
    workspace = args.workspace
    if workspace is None and (Path.cwd() / "scopecat.toml").is_file():
        workspace = Path.cwd()
    try:
        status = launch_notebook(workspace, args.home, no_browser=args.no_browser)
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(2, f"{error}\n")
    if status:
        parser.exit(status)


if __name__ == "__main__":
    main()
