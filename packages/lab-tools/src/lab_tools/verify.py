"""Exercise the installed course with separate kernels, without repository code."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Protocol, cast


class VerifyArguments(Protocol):
    project: Path
    static_dir: Path | None


def execute_project(destination: Path, *, static_dir: Path | None = None) -> Path:
    """Create a fresh synthetic project and retain executed notebooks as evidence."""
    try:
        from .notebook_io import notebook_io

        nbformat = notebook_io()
        from nbclient import NotebookClient
    except ImportError as error:
        raise ValueError("请先安装 scopecat-lab-tools[kernel] 再执行 verify") from error

    import httpx2

    import scopecat as sc
    from scopecat_server.lifecycle import start_project, stop_project

    from .project import notebook_command
    from .verify_editing import EDIT_CELLS, REOPEN_CELLS
    from .verify_groups import GROUP_CELLS, GROUP_REOPEN_CELLS

    root = destination.resolve()
    _, env = notebook_command(root)
    previous = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = env["JUPYTER_PATH"]
    project = sc.open_project(root)
    try:
        record = start_project(project, static_dir=static_dir, timeout=300)
        if static_dir is not None:
            response = httpx2.get(record.base_url)
            _ = response.raise_for_status()
            if "<html" not in response.text:
                raise ValueError("GUI 入口未返回 HTML")
        for name in (
            "start",
            "reopen",
            "groups",
            "groups-reopen",
            "editing",
            "editing-reopen",
        ):
            if name in ("start", "reopen"):
                notebook = nbformat.read(root / f"notebooks/{name}.ipynb", as_version=4)
                checks = (
                    "assert list(params) == ['teaching_drive']\n"
                    "catalog = [item.id for item in session.catalog().entries]\n"
                    "assert catalog == ['teaching.rabi']\n"
                    "assert report.status == 'passed'\n"
                    "assert abs(report.pi_amplitude - 0.24) < 0.02\n"
                    "assert narrow_report.pi_amplitude is None\n"
                    "assert silent_report.status == 'fit_failed'\n"
                    "assert validation_report.status == 'passed'\n"
                    "again = analyze_rabi(session, run)\n"
                    "assert again.pi_amplitude == report.pi_amplitude\n"
                    if name == "start"
                    else "assert run.id == bookmark['run_id']\n"
                    "assert drive['q0'].frequency == 5.15\n"
                )
                cast("list[object]", notebook.cells).append(
                    nbformat.v4.new_code_cell(checks + "session.close()\n")
                )
            else:
                cells = {
                    "editing": EDIT_CELLS,
                    "editing-reopen": REOPEN_CELLS,
                    "groups": GROUP_CELLS,
                    "groups-reopen": GROUP_REOPEN_CELLS,
                }[name]
                notebook = nbformat.v4.new_notebook(
                    cells=[nbformat.v4.new_code_cell(cell) for cell in cells]
                )
            print(f"执行 {name}.ipynb", flush=True)
            try:
                _ = NotebookClient(
                    notebook,
                    timeout=300,
                    startup_timeout=300,
                    kernel_name="scopecat-lab",
                    resources={
                        "metadata": {
                            "path": str(root if name == "start" else root / "notebooks")
                        }
                    },
                ).execute()
            finally:
                nbformat.write(notebook, root / f"notebooks/verified-{name}.ipynb")
            if name in ("start", "groups", "editing"):
                _ = stop_project(project)
                _ = start_project(project, static_dir=static_dir, timeout=300)
    finally:
        try:
            _ = stop_project(project)
        finally:
            if previous is None:
                _ = os.environ.pop("JUPYTER_PATH", None)
            else:
                os.environ["JUPYTER_PATH"] = previous
    print(
        "合成课程、免命名保存、分组与研究目录、统一刷新、新增实验、"
        "平均 IQ 与 Unit 读取及独立内核重开通过; "
        "不代表实机或真人体验验收",
        flush=True,
    )
    return root


def verify_project(destination: Path, *, static_dir: Path | None = None) -> Path:
    from .environment import prepare_project
    from .project import create_project
    from .verify_maintenance import verify_copies

    root = create_project(destination).parent
    python = prepare_project(root, bundle=static_dir.parent if static_dir else None)
    command = [str(python), "-m", "lab_tools.verify", str(root)]
    if static_dir is not None:
        command.extend(("--static-dir", str(static_dir)))
    env = dict(os.environ)
    _ = env.pop("PYTHONPATH", None)
    _ = subprocess.run(command, env=env, check=True)  # noqa: S603 - explicit local tool and argument list
    verify_copies(root, static_dir=static_dir)
    return root


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    _ = parser.add_argument("project", type=Path)
    _ = parser.add_argument("--static-dir", type=Path)
    args = cast("VerifyArguments", cast("object", parser.parse_args()))
    _ = execute_project(args.project, static_dir=args.static_dir)
