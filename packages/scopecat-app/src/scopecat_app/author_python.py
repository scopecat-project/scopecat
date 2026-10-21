"""Resolve independent author interpreters without changing shell or global packages."""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, cast

from packaging.specifiers import SpecifierSet
from uv import find_uv_bin

from .bundle import file_hash

if TYPE_CHECKING:
    from .application_runtime import ApplicationRuntime


def run(command: list[str]) -> str:
    result = subprocess.run(  # noqa: S603 - explicit interpreter/dependency operation
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise ValueError("依赖准备未完成，原环境保留：\n" + result.stderr[-8192:])
    return result.stdout.strip()


def check(runtime: ApplicationRuntime, workspace: Path, python: Path) -> Path:
    """Check version and ownership only; dependency/worker capture is separate."""
    info = cast(
        "dict[str, str]",
        json.loads(
            run(
                [
                    str(python),
                    "-I",
                    "-c",
                    (
                        "import json,sys; print(json.dumps(dict("
                        "version=sys.version.split()[0], base=sys.base_prefix, "
                        "executable=sys.executable)))"
                    ),
                ]
            )
        ),
    )
    application = runtime.installation().python.absolute()
    app_prefix = (
        application.parent.parent
        if application.parent.name == "bin"
        else application.parent
    )
    if Path(info["base"]).resolve() == app_prefix.resolve() or Path(
        info["executable"]
    ).resolve().is_relative_to(app_prefix.resolve()):
        raise ValueError("作者 Python 不能使用应用内部解释器；请选择独立 Python")
    requirement = ">=3.14"
    declaration = workspace / "pyproject.toml"
    if declaration.is_file():
        document = cast(
            "dict[str, dict[str, object]]",
            tomllib.loads(declaration.read_text(encoding="utf-8")),
        )
        requirement += "," + str(
            document.get("project", {}).get("requires-python", ">=3.14")
        )
    if not SpecifierSet(requirement).contains(info["version"], prereleases=True):
        raise ValueError(
            f"作者 Python {info['version']} 不满足 {requirement}；请明确选择兼容解释器"
        )
    return python


def resolve(
    runtime: ApplicationRuntime,
    workspace: Path,
    bundle: Path,
    *,
    python: Path | None = None,
    offline: bool = False,
) -> Path:
    """Use an explicit interpreter or a pinned uv-managed Python, never the host."""
    if python is not None:
        return check(runtime, workspace, python.absolute())
    configured = os.environ.get("UV_PYTHON")
    if configured:
        found = run(
            [
                find_uv_bin(),
                "python",
                "find",
                configured,
                "--system",
                "--no-project",
                "--no-python-downloads",
            ]
        )
        return check(runtime, workspace, Path(found))
    pin = workspace / ".python-version"
    recipe = bundle / "author.json"
    default = (
        str(
            cast("dict[str, object]", json.loads(recipe.read_text(encoding="utf-8")))[
                "python"
            ]
        )
        if recipe.is_file()
        else platform.python_version()
    )
    request = pin.read_text(encoding="utf-8").strip() if pin.is_file() else default
    if not re.fullmatch(r"(?:cpython@)?3\.\d+(?:\.\d+)?t?", request):
        raise ValueError(
            "请在 .python-version 固定 Python 版本，或明确选择已有 Python；"
            "不会自动选择 latest"
        )
    uv = find_uv_bin()
    command = [
        uv,
        "python",
        "find",
        request,
        "--system",
        "--no-project",
        "--managed-python",
        "--no-python-downloads",
    ]
    try:
        found = run(command)
    except ValueError:
        if offline:
            raise ValueError(
                "离线缓存没有所需 Python；请联网准备或明确选择已有 Python"
            ) from None
        try:
            run([uv, "python", "install", request, "--no-bin", "--no-registry"])
        except ValueError as error:
            raise ValueError(
                f"无法获取所需的独立 Python {request}。请检查下载错误；"
                "可通过应用 CLI 的 --python 或 UV_PYTHON 明确选择已有的兼容独立解释器，"
                "不要使用应用内部 Python。原环境保留，不会自动改用其他版本。\n"
                f"{error}"
            ) from error
        found = run(command)
    return check(runtime, workspace, Path(found))


def identity(python: Path) -> dict[str, object]:
    """Fingerprint the selected base binary and ABI, not just its mutable path."""
    info = cast(
        "dict[str, str]",
        json.loads(
            run(
                [
                    str(python),
                    "-I",
                    "-c",
                    (
                        "import json,sys,sysconfig; from pathlib import Path; "
                        "suffix = 't' if sysconfig.get_config_var('Py_GIL_DISABLED')"
                        " else ''; "
                        "library = (Path(sys.base_prefix) / "
                        "f'python{sys.version_info.major}"
                        "{sys.version_info.minor}{suffix}.dll' "
                        "if sys.platform == 'win32' else "
                        "Path(sysconfig.get_config_var('LIBDIR') or sys.base_prefix) / "
                        "(sysconfig.get_config_var('LDLIBRARY') or '')); "
                        "print(json.dumps(dict(version=sys.version, "
                        "implementation=sys.implementation.name, "
                        "abi=sysconfig.get_config_var('SOABI'), "
                        "base=sys.base_prefix, "
                        "binary=getattr(sys,'_base_executable',sys.executable), "
                        "library=str(library))))"
                    ),
                ]
            )
        ),
    )
    return {
        **info,
        "binary_sha256": file_hash(Path(info["binary"])),
        "library_sha256": file_hash(Path(info["library"]))
        if Path(info["library"]).is_file()
        else None,
    }
