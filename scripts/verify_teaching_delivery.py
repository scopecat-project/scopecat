"""在无仓库导入路径、空缓存的独立环境中验收教学交付。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast


class Task(TypedDict):
    label: str
    command: str
    args: list[str]


class Editor(TypedDict):
    tasks: list[Task]


class Cell(TypedDict):
    cell_type: str
    source: list[str]


class Notebook(TypedDict):
    cells: list[Cell]


def verify(bundle: Path, destination: Path) -> None:
    from lab_tools.bundle import configure_console

    configure_console()
    bundle, destination = bundle.resolve(), destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(
        UV_OFFLINE="1",
        UV_CACHE_DIR=str(destination / "empty-cache"),
        PYTHONUTF8="1",
    )
    bootstrap = destination / "bootstrap"
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [sys.executable, str(bundle / "install.py"), str(bootstrap)],
        cwd=destination,
        env=env,
        check=True,
    )
    python = bootstrap / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    console = python.with_name("scopecat.exe" if os.name == "nt" else "scopecat")
    for command_args in (
        ("--help",),
        ("app", "--help"),
        ("notebook", "--help"),
        ("teach", "--help"),
    ):
        subprocess.run(  # noqa: S603 - installed public entry, no side effects
            [str(console), *command_args],
            cwd=destination,
            env=env,
            check=True,
        )
    command = [str(python), "-m", "lab_tools.cli"]
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [*command, "verify", str(destination / "中文 教材")],
        cwd=destination,
        env=env,
        check=True,
    )
    project = destination / "编辑器 准备"
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [*command, "create", str(project)], cwd=destination, env=env, check=True
    )
    tasks = cast(
        "Editor",
        json.loads((project / ".vscode/tasks.json").read_text(encoding="utf-8")),
    )
    task = next(t for t in tasks["tasks"] if t["label"] == "首次准备项目环境")
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [
            task["command"],
            *[a.replace("${workspaceFolder}", str(project)) for a in task["args"]],
        ],
        cwd=project,
        env=env,
        check=True,
    )
    notebook = cast(
        "Notebook",
        json.loads((project / "notebooks/start.ipynb").read_text(encoding="utf-8")),
    )
    cell = next(c for c in notebook["cells"] if c["cell_type"] == "code")
    result = subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [str(python), "-c", "".join(cell["source"])],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode == 0 or "Select Kernel" not in result.stderr:
        raise RuntimeError(f"错误内核未正确拒绝: {result.stdout}\n{result.stderr}")
    home = destination / "application-state"
    subprocess.run(  # noqa: S603 - isolated installed interpreter and fixed check
        [
            str(python),
            str(Path(__file__).with_name("verify_installed_application.py")),
            str(home),
            str(destination / "application"),
            str(bundle / "gui"),
        ],
        cwd=destination,
        env=env,
        check=True,
    )
    (destination / "acceptance.json").write_text(
        json.dumps(
            {
                "build_id": json.loads(
                    (bundle / "bundle.json").read_text(encoding="utf-8")
                )["build_id"],
                "software": "passed",
                "human": "not-evaluated",
                "physical": "not-evaluated",
                "practice": "synthetic scan and manual decision",
                "same_service_cleanup": "passed",
                "no_management_service": "passed",
                "managed_cleanup": "passed",
                "installed_application": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        "离线环境、教材重开与恢复、编辑器准备、错误内核、应用启停与练习清理验收通过",
        flush=True,
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
