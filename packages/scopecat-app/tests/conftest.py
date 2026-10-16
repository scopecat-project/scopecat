"""Shared isolation for local teaching Notebook author imports."""

import json
import os
import subprocess
import sys
import sysconfig
import venv
from collections.abc import Iterator
from pathlib import Path

import pytest

from scopecat_app import bundle


@pytest.fixture
def notebook_imports(monkeypatch: pytest.MonkeyPatch) -> Iterator[pytest.MonkeyPatch]:
    # The reference fixture keeps its endpoint override for the whole test session.
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    previous = {
        name: module
        for name, module in sys.modules.copy().items()
        if name in ("my_experiment", "workspace_app")
        or name.startswith("my_experiment.")
    }
    finders = list(sys.meta_path)
    try:
        yield monkeypatch
    finally:
        sys.meta_path[:] = finders
        for name in tuple(sys.modules):
            if name in ("my_experiment", "workspace_app") or name.startswith(
                "my_experiment."
            ):
                del sys.modules[name]
        sys.modules.update(previous)


@pytest.fixture
def delivery(tmp_path: Path) -> Path:
    root = tmp_path / "delivery"
    (root / "gui").mkdir(parents=True)
    (root / "wheels").mkdir()
    (root / "gui/index.html").write_text("<html>current GUI</html>")
    (root / "wheels/example.whl").write_bytes(b"wheel fixture")
    (root / "requirements.lock").write_text("example==1\n")
    (root / "dependencies.lock").write_text("example==1\n")
    (root / "build.lock").write_text("version = 1\n")
    (root / "install.py").write_text("# installer fixture\n")
    files = bundle.inventory(root, ("gui", "wheels"))
    files.update(
        {
            name: bundle.file_hash(root / name)
            for name in (
                "requirements.lock",
                "dependencies.lock",
                "build.lock",
                "install.py",
            )
        }
    )
    (root / bundle.MANIFEST).write_text(
        json.dumps(
            {
                "format": 1,
                "target": bundle.target_identity(),
                "sources": {},
                "runtime": {"scopecat": "current"},
                "files": files,
            }
        )
    )
    return root


@pytest.fixture
def author_kernel_python(tmp_path: Path) -> Path:
    """A real author venv distinct from the application's test interpreter."""
    root = tmp_path / "author-kernel"
    venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(root)
    python = root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    purelib = Path(
        subprocess.check_output(  # noqa: S603 -- fixed test-owned interpreter
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
    return python
