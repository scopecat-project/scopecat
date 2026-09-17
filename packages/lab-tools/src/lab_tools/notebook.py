"""Bind Jupyter to the launcher Python and explicit project source directory."""

import json
import os
import sys
from pathlib import Path


def kernel_command(
    project: Path, *, python: str = sys.executable, source_path: bool = True
) -> tuple[list[str], dict[str, str]]:
    """Pin the notebook kernel to the launcher interpreter, not a global kernel."""
    project = project.resolve()
    kernel_root = project / ".scopecat-notebook" / "kernels" / "scopecat-lab"
    kernel_root.mkdir(parents=True, exist_ok=True)
    _ = (kernel_root / "kernel.json").write_text(
        json.dumps(
            {
                "argv": [
                    python,
                    "-m",
                    "ipykernel_launcher",
                    "-f",
                    "{connection_file}",
                ],
                "display_name": "Scopecat 实验环境",
                "language": "python",
                "env": {
                    "PYTHONPATH": os.pathsep.join(
                        filter(
                            None,
                            (str(project / "src"), os.environ.get("PYTHONPATH", "")),
                        )
                    )
                }
                if source_path
                else {},
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    env["JUPYTER_PATH"] = os.pathsep.join(
        filter(None, (str(kernel_root.parents[1]), env.get("JUPYTER_PATH", "")))
    )
    return [
        python,
        "-m",
        "jupyterlab",
        f"--ServerApp.root_dir={project}",
        "--MappingKernelManager.default_kernel_name=scopecat-lab",
        "--KernelSpecManager.allowed_kernelspecs=scopecat-lab",
    ], env


def project_python(project: Path) -> Path:
    python = (
        project
        / ".venv"
        / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    )
    if not python.is_file():
        raise ValueError("项目环境尚未准备; 请先执行 scopecat-lab prepare <项目目录>")
    return python
