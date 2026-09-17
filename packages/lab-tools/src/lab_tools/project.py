"""Installed environment identity and fresh teaching-project admission."""

import json
import tomllib
from pathlib import Path
from typing import TypedDict, cast

from scopecat.installed_authors import capture_installed_authors

from .notebook import kernel_command, project_python

METADATA = "author-environment.json"
PACKAGES = (
    ("scopecat", "scopecat"),
    ("scopecat_server", "scopecat-server"),
    ("lab_teaching", "scopecat-lab-teaching"),
)


class LabManifest(TypedDict):
    lab: dict[str, object]


def environment_identity() -> dict[str, object]:
    # Reuse the framework's content identity, including same-version wheel edits.
    return {
        name: value.model_dump(mode="json")
        for name, value in capture_installed_authors(PACKAGES).items()
    }


def create_project(destination: Path, *, topic: str | None = None) -> Path:
    if destination.exists():
        raise FileExistsError(f"目标目录已存在: {destination}; 请使用新目录")
    if topic is not None:
        from lab_teaching.lessons import TOPICS

        if topic not in TOPICS:
            raise ValueError(f"未知专题: {topic}")
    environment = environment_identity()
    from lab_teaching.project import create_project as create_teaching

    manifest = create_teaching(destination)
    if topic is not None:
        from lab_teaching.lessons import install_lesson

        _ = install_lesson(manifest.parent, topic)
    _ = (manifest.parent / METADATA).write_text(
        json.dumps(
            {"format": 3, "kind": "teaching", "environment": environment},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return manifest


def check_project(project: Path) -> Path:
    project = project.resolve()
    path = project / METADATA
    if not path.is_file():
        raise ValueError("缺少交付环境记录; 请先 create 新目录; 旧项目保留原环境")
    metadata = cast("dict[str, object]", json.loads(path.read_text(encoding="utf-8")))
    if metadata.get("format") != 3 or metadata.get("kind") != "teaching":
        raise ValueError("此入口只打开独立 wheel 教学项目; 旧项目使用原入口和环境")
    if metadata.get("environment") != environment_identity():
        raise ValueError("安装源码与项目记录不同; 保留原环境; 本入口不迁移旧库")
    manifest = cast(
        "LabManifest",
        cast(
            "object",
            tomllib.loads((project / "scopecat.toml").read_text(encoding="utf-8")),
        ),
    )
    if (
        manifest["lab"].get("application") != "workspace_app:create_application"
        or "instrument_backend" in manifest["lab"]
    ):
        raise ValueError("此入口只用于无设备的最小教学项目")
    return project


def notebook_command(project: Path) -> tuple[list[str], dict[str, str]]:
    root = check_project(project)
    return kernel_command(root, python=str(project_python(root)), source_path=False)
