"""Minimal project fixture for focused scientific and kernel regression tests."""

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


def create_project(destination: str | Path, *, topic: str | None = None) -> Path:
    from .lessons import TOPICS, install_lesson

    if topic is not None and topic not in TOPICS:
        raise ValueError(f"未知专题: {topic}")
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    _ = (destination / "pyproject.toml").write_text(PYPROJECT, encoding="utf-8")
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
    _ = (destination / "README.md").write_text(
        "# Teaching regression fixture\n\n"
        "This scaffold is used by focused source and kernel tests. "
        "Ordinary learning starts in Scopecat Help; editable CLI source uses "
        "scopecat init --topic. Existing folders retain their original environment "
        "and data; this fixture does not migrate them.\n",
        encoding="utf-8",
    )
    return manifest
