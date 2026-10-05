"""Create a minimal user project using installed resources only."""

import json
import sys
from pathlib import Path

MANIFEST = """[lab]
bootstrap = "workspace_app:create_bootstrap"

[lab.capabilities]
author_modules = ["my_experiment"]

[authors]
source_roots = ["src"]
refresh_roots = ["src/my_experiment"]
dependencies = []

[authors.packages]
lab_teaching = "scopecat-lab-teaching"
"""

PYPROJECT = """[project]
name = "my-experiment"
version = "0.1.0"
requires-python = ">=3.14"
dependencies = ["scopecat", "scopecat-lab-teaching"]

[build-system]
requires = ["uv_build>=0.12.3,<0.13"]
build-backend = "uv_build"

[tool.uv.build-backend]
module-name = "my_experiment"
"""


def write_editor_files(destination: Path) -> None:
    editor = destination / ".vscode"
    editor.mkdir()
    tasks: list[dict[str, object]] = []
    for command, label in (
        ("start", "启动实验服务"),
        ("open", "打开实验界面"),
        ("stop", "停止实验服务"),
    ):
        tasks.append(
            {
                "label": label,
                "type": "process",
                "command": "${workspaceFolder}/.venv/bin/python",
                "windows": {"command": "${workspaceFolder}/.venv/Scripts/python.exe"},
                "args": ["-m", "lab_tools.cli", command, "${workspaceFolder}"],
                "problemMatcher": [],
            }
        )
    tasks[1]["dependsOn"] = "启动实验服务"
    tasks.append(
        {
            "label": "首次准备项目环境",
            "type": "process",
            "command": sys.executable,
            "args": ["-m", "lab_tools.cli", "prepare", "${workspaceFolder}"],
            "problemMatcher": [],
        }
    )
    documents: dict[str, object] = {
        "settings.json": {"python.defaultInterpreterPath": "${workspaceFolder}/.venv"},
        "extensions.json": {
            "recommendations": ["ms-python.python", "ms-toolsai.jupyter"]
        },
        "tasks.json": {"version": "2.0.0", "tasks": tasks},
    }
    for name, document in documents.items():
        _ = (editor / name).write_text(
            json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )


def create_project(destination: str | Path, *, topic: str | None = None) -> Path:
    from .lessons import TOPICS, install_lesson

    if topic is not None and topic not in TOPICS:
        raise ValueError(f"未知专题: {topic}")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    _ = (destination / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
    write_editor_files(destination)
    source = destination / "src/my_experiment"
    source.mkdir(parents=True)
    _ = (source / "__init__.py").write_text("", encoding="utf-8")
    _ = (destination / ".gitignore").write_text(
        ".venv/\n.scopecat/\n.scopecat-notebook/\n**/__pycache__/\n.ipynb_checkpoints/\n",
        encoding="utf-8",
    )
    manifest = destination / "scopecat.toml"
    _ = manifest.write_text(MANIFEST, encoding="utf-8")
    (destination / "notebooks").mkdir()
    _ = install_lesson(destination, topic)
    return manifest
