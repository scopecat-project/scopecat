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
        if topic == "refresh":
            # A genuinely new kernel must reopen retained runs without relying on
            # any variable from the lesson or launching the experiments again.
            with project.authoring() as reader:
                expected = {
                    item.control.sequence: item.run_id
                    for item in reader.list_runs(limit=20).items
                }
            assert expected
            reopen = nbformat.v4.new_notebook(
                cells=[
                    nbformat.v4.new_code_cell(
                        "import scopecat as sc\n"
                        "session = sc.notebook()\n"
                        "assert sc.notebook() is session\n"
                        "session.history()"
                    ),
                    nbformat.v4.new_code_cell(
                        f"expected = {expected!r}\n"
                        "for number, identity in expected.items():\n"
                        "    restored = session.run(number)\n"
                        "    assert restored.id == identity\n"
                        "    assert len(restored.measurements()) == 2\n"
                        "retained = session.list_runs(limit=20).items\n"
                        "assert len(retained) == len(expected)\n"
                        "session.close()"
                    ),
                ]
            )
            try:
                NotebookClient(
                    reopen,
                    timeout=300,
                    startup_timeout=300,
                    kernel_name="scopecat-lab",
                    resources={"metadata": {"path": str(root / "notebooks")}},
                ).execute()
            finally:
                nbformat.write(reopen, root / "notebooks/verified-refresh-reopen.ipynb")
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
