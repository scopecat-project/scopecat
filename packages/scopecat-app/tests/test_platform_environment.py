"""Thin real dependency preparation, without building a native application."""

import json
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

from scripts.app_build.author_resources import build
from uv import find_uv_bin

from scopecat_app.application_runtime import ApplicationRuntime, Installation
from scopecat_app.author_environment import (
    create_client_environment,
    prepare_execution_environment,
)
from scopecat_app.author_resources import verify_resources
from scopecat_app.bundle import MANIFEST, file_hash, inventory, target_identity


def test_prepared_execution_python_imports_pyarrow(tmp_path: Path):
    # Keep pytest's normal OS temp root and descriptive test directory. In #871
    # a nested content-hash environment broke Windows DLL loading at this depth.
    # Qualify the runner's installed interpreter explicitly: this checks isolated
    # environments and native imports, not uv's release-dependent download catalog.
    # A newly released setup-python patch may not yet have a managed uv build.
    # The pytest venv represents the application. Its underlying runner Python
    # is outside that venv, including Windows where python.exe is not a symlink.
    base_python = Path(getattr(sys, "_base_executable", sys.executable))
    root = Path(__file__).resolve().parents[3]
    payload = tmp_path / "payload"
    wheels = payload / "wheels"
    wheels.mkdir(parents=True)
    for package in ("scopecat", "scopecat-server"):
        subprocess.run(  # noqa: S603 - owned uv/interpreter and fixed arguments
            [
                find_uv_bin(),
                "build",
                "--package",
                package,
                "--wheel",
                "--out-dir",
                str(wheels),
            ],
            cwd=root,
            check=True,
        )
    (payload / "gui").mkdir()
    (payload / "gui/index.html").write_text("smoke fixture", encoding="utf-8")
    files = inventory(payload, ("gui", "wheels"))
    for name in ("requirements.lock", "dependencies.lock", "build.lock", "install.py"):
        (payload / name).write_text("", encoding="utf-8")
        files[name] = file_hash(payload / name)
    (payload / MANIFEST).write_text(
        json.dumps(
            {
                "format": 1,
                "target": target_identity(),
                "sources": {},
                "runtime": {},
                "files": files,
            }
        ),
        encoding="utf-8",
    )
    bundle = build(payload, tmp_path / "bundle")
    verify_resources(bundle)
    assert not (bundle / "toolchain/python.tar").exists()
    assert len(list((bundle / "wheels").glob("*.whl"))) == 2
    runtime = ApplicationRuntime(tmp_path / "application-data")
    runtime.selection.parent.mkdir(parents=True)
    runtime.selection.write_text(
        Installation(
            python=Path(sys.executable),
            static_dir=bundle / "gui",
            delivery_root=bundle,
            delivery_manifest_sha256=file_hash(bundle / MANIFEST),
            environment={
                "scopecat": version("scopecat"),
                "server": version("scopecat-server"),
            },
        ).model_dump_json(),
        encoding="utf-8",
    )
    source = tmp_path / "ordinary-author-source"
    source.mkdir()
    (source / "pyproject.toml").write_text(
        '[project]\nname="platform-smoke-author"\nversion="0.1.0"\ndependencies=["pyarrow"]\n',
        encoding="utf-8",
    )
    (source / "scopecat.toml").write_text(
        '[authors]\nsource_roots=["src"]\nmodules=["experiment"]\ndependencies=["pyarrow"]\n',
        encoding="utf-8",
    )
    (source / "src").mkdir()
    (source / "src/experiment.py").write_text("import pyarrow\n", encoding="utf-8")
    python = prepare_execution_environment(runtime, source, python=base_python)
    result = subprocess.run(  # noqa: S603 - owned uv/interpreter and fixed arguments
        [
            str(python),
            "-I",
            "-c",
            (
                "import json,sys,pyarrow; "
                "print(json.dumps([sys.prefix, pyarrow.__version__]))"
            ),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    prefix, arrow_version = json.loads(result.stdout)
    assert Path(prefix) == python.parent.parent
    assert arrow_version
    assert prepare_execution_environment(runtime, source, python=base_python) == python

    client = create_client_environment(runtime, source, python=base_python)
    subprocess.run(  # noqa: S603 - real independent client qualification
        [str(client), "-I", "-c", "import scopecat, ipykernel, pyarrow, pip"],
        check=True,
    )
    assert client != python
    assert create_client_environment(runtime, source, python=base_python) == client
    assert not (source / ".scopecat-python").exists()
