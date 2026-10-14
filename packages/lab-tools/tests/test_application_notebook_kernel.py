"""An application interpreter is not an author kernel, even after registration."""

# ruff: noqa: S603 -- fixed local interpreters and test-owned source paths

import os
import subprocess
import sys
import sysconfig
import venv
from pathlib import Path

import pytest

from lab_teaching.project import create_project
from lab_tools.application_runtime import ApplicationRuntime


def test_registered_notebook_rejects_application_but_accepts_external_kernel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = create_project(tmp_path / "author", topic="parameters").parent
    retained = root / ".venv/retained.txt"
    retained.parent.mkdir()
    retained.write_text("previous environment", encoding="utf-8")
    gui = tmp_path / "gui"
    gui.mkdir()
    (gui / "index.html").write_text("<html>workbench</html>", encoding="utf-8")
    runtime = ApplicationRuntime(tmp_path / "application")
    runtime.configure(static_dir=gui)
    runtime.register_source(root)

    # Use a distinct real venv, sharing installed packages only for this test.
    # On POSIX its executable resolves to the same base binary as the app's.
    external = tmp_path / "external"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(external)
    python = external / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    purelib = Path(
        subprocess.check_output(
            [
                str(python),
                "-c",
                "import sysconfig; print(sysconfig.get_path('purelib'))",
            ],
            text=True,
        ).strip()
    )
    (purelib / "test-dependencies.pth").write_text(
        f"import site; site.addsitedir({sysconfig.get_path('purelib')!r})\n",
        encoding="utf-8",
    )
    code = (
        "from IPython.core.interactiveshell import InteractiveShell; "
        "InteractiveShell.instance(); import scopecat as sc; "
        f"session = sc.notebook({str(root)!r}); "
        "print('author connected'); session.close()"
    )
    try:
        runtime.start()
        wrong = subprocess.run(
            [sys.executable, "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert wrong.returncode != 0
        assert "Select Kernel" in wrong.stderr
        assert "not the application's Python" in wrong.stderr
        assert "author connected" not in wrong.stdout
        right = subprocess.run(
            [str(python), "-I", "-c", code],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
        assert right.returncode == 0, right.stdout + right.stderr
        assert "author connected" in right.stdout
        assert retained.read_text(encoding="utf-8") == "previous environment"
    finally:
        runtime.stop()
