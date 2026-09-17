"""Prepare an isolated local author environment from a verified delivery."""

import shutil
import subprocess
from pathlib import Path

from .bundle import gui_directory, install_bundle
from .notebook import project_python
from .project import check_project, environment_identity


def prepare_project(project: Path, *, bundle: Path | None = None) -> Path:
    project = check_project(project)
    delivery = gui_directory(bundle, environment_identity()).parent
    uv = shutil.which("uv")
    if uv is None:
        raise ValueError("项目环境准备需要 uv")
    # Existing environments are deliberately not synchronized or overwritten.
    _ = install_bundle(delivery, project / ".venv")
    python = project_python(project)
    _ = subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [
            uv,
            "pip",
            "install",
            "--offline",
            "--no-index",
            "--no-deps",
            "--no-build-isolation",
            "--python",
            str(python),
            "--editable",
            str(project),
        ],
        check=True,
    )
    _ = subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [str(python), "-m", "lab_tools.cli", "check", str(project)],
        check=True,
    )
    return python
