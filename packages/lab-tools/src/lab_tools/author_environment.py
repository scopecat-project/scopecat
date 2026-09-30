"""Independent editable client environments and retained execution environments."""

from __future__ import annotations

import json
import os
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from uv import find_uv_bin

from scopecat.kernel.content_identity import sha256_json_hash

from .bundle import MANIFEST, file_hash, install_bundle, verify_bundle

if TYPE_CHECKING:
    from .application_runtime import ApplicationRuntime


def environment_python(environment: Path) -> Path:
    return environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _run(command: list[str]) -> None:
    result = subprocess.run(  # noqa: S603 - explicit dependency maintenance, never a shell
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("依赖准备未完成，原环境保留：\n" + result.stderr[-8192:])


def _bundle(runtime: ApplicationRuntime) -> Path:
    return runtime.installation().static_dir.parent


def _independent_python(bundle: Path, home: Path) -> Path:
    """Retain a base interpreter owned by the environment, not the application."""
    archive = bundle / "toolchain/python.tar"
    if not archive.is_file():
        raise ValueError("创建独立作者环境需要包含 Python 的平台交付包")
    key = file_hash(archive).split(":")[-1]
    base = home / key
    home.mkdir(parents=True, exist_ok=True)
    from filelock import FileLock

    with FileLock(home / "python.lock"):
        if not base.exists():
            _ = verify_bundle(bundle)
            with tempfile.TemporaryDirectory(prefix=".python-", dir=home) as temporary:
                staged = Path(temporary) / "runtime"
                with tarfile.open(archive) as stream:
                    stream.extractall(staged, filter="data")
                staged.rename(base)
    return base / ("python.exe" if os.name == "nt" else "bin/python3")


def create_client_environment(
    runtime: ApplicationRuntime, workspace: Path, *, rebuild: bool = False
) -> Path:
    """Seed a normal user-owned venv once; application updates never sync it."""
    environment = workspace / ".venv"
    if environment.is_symlink():
        raise ValueError("作者 .venv 必须是独立目录，不能链接到应用环境")
    bundle = _bundle(runtime)
    previous: Path | None = None
    if rebuild and environment.exists():
        previous = environment.rename(workspace / f".venv-retained-{uuid4().hex}")
    if environment.exists():
        python = environment_python(environment)
        if not python.is_file():
            raise ValueError(
                f"作者环境不完整，原目录保留，请重建自己的环境：{environment}"
            )
        return python
    try:
        base_python = _independent_python(bundle, workspace / ".scopecat-python")
        _ = install_bundle(
            bundle,
            environment,
            copy_packages=True,
            base_python=base_python,
            packages=("scopecat", "ipykernel"),
        )
        python = environment_python(environment)
        _run([str(python), "-m", "ensurepip"])
    except Exception:
        if environment.exists():
            environment.rename(workspace / f".venv-failed-{uuid4().hex}")
        if previous is not None:
            previous.rename(environment)
        raise
    declaration = workspace / "pyproject.toml"
    if not declaration.exists():
        declaration.write_text(
            '[project]\nname = "scopecat-experiments"\nversion = "0.1.0"\n'
            'requires-python = ">=3.14"\ndependencies = []\n\n'
            "# Add dependencies needed by background experiments here.\n"
            "# Local pip installs affect only this folder, not the application.\n",
            encoding="utf-8",
        )
    return python


def prepare_execution_environment(
    runtime: ApplicationRuntime, workspace: Path, *, offline: bool = False
) -> Path:
    """Resolve declared additions against a fixed delivery without editing it.

    The returned environment is application-owned. Existing published source and
    pending tasks retain their previous interpreters when this candidate is selected.
    """
    workspace = workspace.resolve()
    declaration = workspace / "pyproject.toml"
    if not declaration.is_file():
        raise ValueError("请在作者目录的 pyproject.toml 声明后台实验所需依赖")
    bundle = _bundle(runtime)
    uv = find_uv_bin()
    with tempfile.TemporaryDirectory(prefix="scopecat-author-lock-") as temporary:
        lock = Path(temporary) / "requirements.lock"
        _run(
            [
                uv,
                "pip",
                "compile",
                str(declaration),
                "--python",
                str(runtime.installation().python),
                "--constraint",
                (bundle / "requirements.lock").as_uri(),
                "--find-links",
                (bundle / "wheels").as_uri(),
                "--generate-hashes",
                "--no-header",
                "--no-annotate",
                "--output-file",
                str(lock),
                *(["--offline"] if offline else []),
            ]
        )
        requirements = lock.read_text(encoding="utf-8")
    key = sha256_json_hash(
        {"delivery": file_hash(bundle / MANIFEST), "requirements": requirements}
    ).split(":")[-1]
    directory = runtime.home / "environments" / key
    ready = directory / "environment.json"
    with runtime.lock:
        if ready.is_file():
            return Path(cast("dict[str, str]", json.loads(ready.read_text()))["python"])
        # Never rename a completed virtual environment: its scripts contain paths.
        attempt = directory / uuid4().hex
        attempt.mkdir(parents=True)
        lock = attempt / "requirements.lock"
        lock.write_text(requirements, encoding="utf-8")
        environment = attempt / "runtime"
        base_python = _independent_python(bundle, runtime.home / "environments/python")
        _ = install_bundle(bundle, environment, base_python=base_python)
        python = environment_python(environment)
        _run(
            [
                uv,
                "pip",
                "install",
                "--python",
                str(python),
                "--require-hashes",
                "--find-links",
                (bundle / "wheels").as_uri(),
                "--constraint",
                (bundle / "requirements.lock").as_uri(),
                "-r",
                str(lock),
                *(["--offline"] if offline else []),
            ]
        )
        from scopecat_server.author_environment import capture

        capture(workspace, python, owner=runtime.root)
        temporary = ready.with_suffix(".tmp")
        temporary.write_text(json.dumps({"python": str(python)}), encoding="utf-8")
        temporary.replace(ready)
        return python
