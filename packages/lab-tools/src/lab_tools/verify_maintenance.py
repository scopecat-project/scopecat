"""在新位置准备独立环境,读回当前格式的恢复副本并追加分析。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import Protocol, cast


def verify_restore(
    root: Path, *, static_dir: Path | None = None, groups: bool = True
) -> None:
    from scopecat.author_workspaces import local_author_workspaces

    from .environment import prepare_project

    source = next(item for item in local_author_workspaces(root) if item.root == root)
    output = root.with_name(root.name + "-maintenance")
    output.mkdir()
    cli = [sys.executable, "-m", "scopecat_server.cli"]
    timings: dict[str, float] = {}
    for arguments in (
        ["snapshot", "create", str(root), str(output / "original")],
        ["snapshot", "verify", str(output / "original")],
        ["snapshot", "restore", str(output / "original"), str(output / "restored")],
    ):
        phase = perf_counter()
        _ = subprocess.run([*cli, *arguments], check=True)  # noqa: S603 - explicit local tool and argument list
        timings[arguments[1]] = perf_counter() - phase
    phase = perf_counter()
    project = output / "restored"
    python = prepare_project(project, bundle=static_dir.parent if static_dir else None)
    timings["prepare_environment"] = perf_counter() - phase
    command = [
        str(python),
        "-m",
        "lab_tools.verify_maintenance",
        str(project),
        "--source-id",
        source.id,
    ]
    if static_dir is not None:
        command.extend(("--static-dir", str(static_dir)))
    if not groups:
        command.append("--without-groups")
    phase = perf_counter()
    _ = subprocess.run(command, check=True)  # noqa: S603 - explicit local tool and argument list
    timings["read_copy"] = perf_counter() - phase
    (output / "verification-phases.json").write_text(
        json.dumps({"groups": groups, "seconds": timings}, indent=2) + "\n",
        encoding="utf-8",
    )
    print("备份副本、独立环境恢复、原结果读回及新增分析通过", flush=True)


def read_copy(
    root: Path, *, source_id: str, static_dir: Path | None = None, groups: bool = True
) -> None:
    import os

    from .notebook_io import notebook_io

    nbformat = notebook_io()
    from nbclient import NotebookClient

    import scopecat as sc
    from scopecat_server.author_registration import register_author_workspace
    from scopecat_server.lifecycle import start_project, stop_project

    from .project import notebook_command
    from .verify_editing import reopen_cells
    from .verify_groups import GROUP_REOPEN_CELLS

    _, env = notebook_command(root)
    previous = os.environ.get("JUPYTER_PATH")
    os.environ["JUPYTER_PATH"] = env["JUPYTER_PATH"]
    project = sc.open_project(root)
    # Snapshot locations are intentionally unbound; explicitly reconnect the
    # restored source identity before requesting new analysis of retained runs.
    register_author_workspace(root, root, identity=source_id)
    cells = (
        *reopen_cells(root),
        *((*GROUP_REOPEN_CELLS, ADD_ANALYSIS) if groups else ()),
    )
    notebook = nbformat.v4.new_notebook(
        cells=[nbformat.v4.new_code_cell(cell) for cell in cells]
    )
    try:
        _ = start_project(project, static_dir=static_dir, timeout=300)
        _ = NotebookClient(
            notebook,
            timeout=300,
            startup_timeout=300,
            kernel_name="scopecat-lab",
            resources={"metadata": {"path": str(root / "notebooks")}},
        ).execute()
    finally:
        nbformat.write(notebook, root / "notebooks/verified-maintenance.ipynb")
        try:
            _ = stop_project(project)
        finally:
            if previous is None:
                _ = os.environ.pop("JUPYTER_PATH", None)
            else:
                os.environ["JUPYTER_PATH"] = previous


ADD_ANALYSIS = """\
session = project.authoring()
# 原 run 使用保留源码;新分析不覆盖之前的分组结果。
new = session.analyze_groups_as(
    bookmark["run_id"], "my_experiment.group_analysis:summarize_curve", CurveSummary,
    by=("amplitude",), fitting="frequency", arguments={"minimum_contrast": 2.0})
assert new.publication.id != bookmark["publication_id"]
assert all(group.value.status == "no_response" for group in new.groups)
old = session.read_groups_as(
    bookmark["run_id"], bookmark["publication_id"], CurveSummary)
assert all(group.value.status == "estimated" for group in old.groups)
session.close()
"""


if __name__ == "__main__":
    import argparse

    class VerifyArguments(Protocol):
        project: Path
        static_dir: Path | None
        source_id: str
        without_groups: bool

    parser = argparse.ArgumentParser()
    _ = parser.add_argument("project", type=Path)
    _ = parser.add_argument("--static-dir", type=Path)
    _ = parser.add_argument("--source-id", required=True)
    _ = parser.add_argument("--without-groups", action="store_true")
    args = cast("VerifyArguments", cast("object", parser.parse_args()))
    read_copy(
        args.project,
        source_id=args.source_id,
        static_dir=args.static_dir,
        groups=not args.without_groups,
    )
