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
    if topic is not None:
        _ = (destination / "README.md").write_text(
            f"# {TOPICS[topic]}\n\n"
            "此路径用于维护者独立验收和既有独立项目；"
            "普通学习推荐从 Scopecat Help 开始。\n"
            "这是命令行生成的独立教学项目。用 VS Code 打开本目录，"
            "首次运行任务“首次准备项目环境”；已有 .venv 时直接使用原环境。\n"
            "运行任务“启动实验服务”，然后打开 "
            f"notebooks/{topic}.ipynb，选择本目录 .venv 内核。"
            "“打开实验界面”任务会先启动或复用服务，再打开 GUI。\n"
            "源码在 src/my_experiment，运行与历史保存在本项目 .scopecat 中。"
            "完成后运行“停止实验服务”；关闭编辑器或内核不会停止服务。\n"
            "Scopecat Help 的课程属于当前应用，不能用其“继续”打开本项目。"
            "再次使用本项目时打开原目录，保留环境、源码和数据。\n",
            encoding="utf-8",
        )
    return manifest
