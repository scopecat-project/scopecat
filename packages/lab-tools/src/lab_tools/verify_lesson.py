"""执行用户实际收到的专题 Notebook, 保留输出并关闭服务。"""

import argparse
import os
from pathlib import Path
from typing import Protocol, cast


class Arguments(Protocol):
    project: Path
    topic: str
    api_only: bool


def execute(root: Path, topic: str, *, api_only: bool = False) -> None:
    from .notebook_io import notebook_io

    nbformat = notebook_io()
    from nbclient import NotebookClient

    import scopecat as sc
    from scopecat_server.lifecycle import start_project, stop_project

    from .bundle import gui_directory
    from .project import environment_identity, notebook_command

    _, env = notebook_command(root)
    old = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = env["JUPYTER_PATH"]
    project = sc.open_project(root)
    notebook = nbformat.read(root / "notebooks" / f"{topic}.ipynb", as_version=4)
    try:
        _ = start_project(
            project,
            static_dir=None
            if api_only
            else gui_directory(None, environment_identity()),
            timeout=300,
        )
        NotebookClient(
            notebook,
            timeout=300,
            startup_timeout=300,
            kernel_name="scopecat-lab",
            resources={"metadata": {"path": str(root / "notebooks")}},
        ).execute()
    finally:
        nbformat.write(notebook, root / "notebooks" / f"verified-{topic}.ipynb")
        try:
            _ = stop_project(project)
        finally:
            if old is None:
                os.environ.pop("JUPYTER_PATH", None)
            else:
                os.environ["JUPYTER_PATH"] = old
    print(f"专题 {topic} 的实际 Notebook 已通过; 合成软件验证, 不代表真人或设备验收。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("project", type=Path)
    _ = parser.add_argument("topic")
    _ = parser.add_argument("--api-only", action="store_true")
    args = cast("Arguments", cast("object", parser.parse_args()))
    execute(args.project.resolve(), args.topic, api_only=args.api_only)
