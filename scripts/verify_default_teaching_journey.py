"""Run the original course in fresh installed kernels after checking resource identity.

Run with a wheel-installed delivery Python (not the editable checkout Python):
    <installed-python> scripts/verify_default_teaching_journey.py
       <fresh-author-directory> <delivery>
"""

import subprocess
import sys
from pathlib import Path

from lab_tools.environment import prepare_project
from lab_tools.project import create_project


def verify(destination: Path, delivery: Path) -> None:
    root = create_project(destination).parent
    material = (
        Path(__file__).resolve().parents[1]
        / "packages/lab-teaching/src/lab_teaching/course_material"
    )
    generated = {
        "src/workspace_app.py": "lessons/workspace_app.py.txt",
        **{
            f"src/my_experiment/{name}.py": f"lessons/{template}.py.txt"
            for name, template in (
                ("parameters", "parameters"),
                ("setup", "setup"),
                ("response", "response"),
                ("teaching", "experiment"),
                ("analysis", "analysis"),
                ("session", "session"),
            )
        },
        **{
            f"src/my_experiment/{name}.py": f"{name}.py"
            for name in ("group_analysis", "result_types")
        },
        **{
            f"notebooks/{name}": name
            for name in (
                "start.ipynb",
                "reopen.ipynb",
                "EDITING.md",
                "GROUPS.md",
            )
        },
    }
    for target, resource in generated.items():
        assert (root / target).read_bytes() == (material / resource).read_bytes(), (
            "Installed teaching material differs from this checkout; run "
            "uv sync --locked --reinstall-package scopecat-lab-teaching"
        )
    python = prepare_project(root, bundle=delivery)
    subprocess.run(  # noqa: S603 - generated environment and fixed verifier module
        [
            str(python),
            "-m",
            "lab_tools.verify",
            str(root),
            "--static-dir",
            str(delivery / "gui"),
        ],
        check=True,
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
