"""Independent editable client environments and retained execution environments."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from contextlib import ExitStack
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
    selected = runtime.installation()
    root = selected.delivery_root
    if root is None or selected.delivery_manifest_sha256 is None:
        raise ValueError(
            "当前应用未登记作者环境资源；请使用完整安装包或选择已有执行 Python"
        )
    if not (root / MANIFEST).is_file():
        raise ValueError("作者环境资源不存在；请恢复原安装包，已有作者环境保留")
    if file_hash(root / MANIFEST) != selected.delivery_manifest_sha256:
        raise ValueError("作者环境资源与已登记交付清单不同；请重新打开当前应用后重试")
    _ = verify_bundle(root)
    return root


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
    if environment.exists() and not rebuild:
        python = environment_python(environment)
        if not python.is_file():
            raise ValueError(
                f"作者环境不完整，原目录保留，请重建自己的环境：{environment}"
            )
        return python
    # Validate before moving the old environment; existing environments need no payload.
    bundle = _bundle(runtime)
    previous: Path | None = None
    if rebuild and environment.exists():
        previous = environment.rename(workspace / f".venv-retained-{uuid4().hex}")
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
    except BaseException:
        # Cancellation must not leave a partial interpreter available for reuse.
        # Restore the user's previous environment before propagating the interrupt.
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
    """Resolve source dependencies with the application's framework API version.

    The returned environment is application-owned. Existing published source and
    pending tasks retain their previous interpreters when this candidate is selected.
    """
    workspace = workspace.resolve()
    declaration = workspace / "pyproject.toml"
    if not declaration.is_file():
        raise ValueError("请在作者目录的 pyproject.toml 声明后台实验所需依赖")
    bundle = _bundle(runtime)
    uv = find_uv_bin()
    framework = runtime.installation().environment
    with tempfile.TemporaryDirectory(prefix="scopecat-author-lock-") as temporary:
        lock = Path(temporary) / "requirements.lock"
        execution = Path(temporary) / "execution.in"
        execution.write_text(
            f"scopecat=={framework['scopecat']}\n"
            f"scopecat-server=={framework['server']}\n",
            encoding="utf-8",
        )
        _run(
            [
                uv,
                "pip",
                "compile",
                str(declaration),
                str(execution),
                "--project",
                str(workspace),
                "--python",
                str(runtime.installation().python),
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
        # Keep the full identity in the receipt, out of native DLL search paths.
        # Atomic allocation avoids collisions without shortening content hashes.
        directory.mkdir(parents=True, exist_ok=True)
        attempt = Path(tempfile.mkdtemp(prefix="e-", dir=directory.parent))
        with ExitStack() as failed:
            failed.callback(shutil.rmtree, attempt)
            lock = attempt / "requirements.lock"
            lock.write_text(requirements, encoding="utf-8")
            environment = attempt / "runtime"
            base_python = _independent_python(
                bundle, runtime.home / "environments/python"
            )
            _run([uv, "venv", "--python", str(base_python), str(environment)])
            python = environment_python(environment)
            _run(
                [
                    uv,
                    "pip",
                    "install",
                    "--project",
                    str(workspace),
                    "--python",
                    str(python),
                    "--require-hashes",
                    "--find-links",
                    (bundle / "wheels").as_uri(),
                    "-r",
                    str(lock),
                    *(["--offline"] if offline else []),
                ]
            )
            from scopecat_server.author_environment import capture

            capture(workspace, python)
            temporary = ready.with_suffix(".tmp")
            temporary.write_text(json.dumps({"python": str(python)}), encoding="utf-8")
            temporary.replace(ready)
            _ = failed.pop_all()
            return python
