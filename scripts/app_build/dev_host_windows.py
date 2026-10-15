"""Cached Windows source host using the current editable Python environment."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import cast

import psutil
from filelock import FileLock
from scripts.app_build.dev_host import MARKER, check_interpreter, clean_environment
from scripts.app_build.dev_resources import development_home

from scopecat_app.bundle import file_hash


def compiler_environment() -> tuple[str, dict[str, str]]:
    """Use an active developer shell or discover the installed VS toolchain."""
    environment = dict(os.environ)
    compiler = shutil.which("cl.exe")
    if compiler:
        return compiler, environment
    vswhere = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / (
        "Microsoft Visual Studio/Installer/vswhere.exe"
    )
    architecture = {"win-amd64": "x64", "win-arm64": "arm64", "win32": "x86"}.get(
        sysconfig.get_platform()
    )
    if vswhere.is_file() and architecture:
        installation = subprocess.check_output(  # noqa: S603 - installed VS discovery
            [
                str(vswhere),
                "-latest",
                "-products",
                "*",
                "-requires",
                "Microsoft.VisualStudio.Component.VC.Tools.x86.x64",
                "-property",
                "installationPath",
            ],
            text=True,
            timeout=30,
        ).strip()
        script = Path(installation) / "VC/Auxiliary/Build/vcvarsall.bat"
        # cmd syntax is necessary for the vendor batch file. Reject metacharacters
        # rather than interpreting an unexpected installation path as shell code.
        if (
            installation
            and script.is_file()
            and not any(character in str(script) for character in '%!&|<>^"\r\n')
        ):
            shell = subprocess.list2cmdline(
                [os.environ.get("COMSPEC", r"C:\Windows\System32\cmd.exe")]
            )
            output = subprocess.check_output(  # noqa: S603 - checked vendor script
                f'{shell} /d /u /s /c ""{script}" {architecture} >nul && set"',
                timeout=60,
            ).decode("utf-16-le")
            environment = dict(
                line.split("=", 1)
                for line in output.splitlines()
                if "=" in line and not line.startswith("=")
            )
            search_path = next(
                (v for k, v in environment.items() if k.lower() == "path"), ""
            )
            compiler = shutil.which("cl.exe", path=search_path)
            if compiler:
                return compiler, environment
    raise ValueError(
        "The Windows source host requires Visual Studio C++ Build Tools and the "
        "Windows SDK. Install the Desktop development with C++ workload, or use "
        "a Native Tools command prompt matching your Python architecture."
    )


def build_host(source: Path) -> Path:
    """Build with MSVC once per interpreter/source identity, outside the checkout."""
    compiler, environment = compiler_environment()
    python = Path(sys.executable).absolute()
    library = Path(sys.base_prefix) / "python3.dll"
    if not library.is_file():
        raise ValueError("The Windows source host requires Python's python3.dll.")
    template = Path(__file__).with_suffix(".c")
    configuration = Path(sys.prefix) / "pyvenv.cfg"
    identity: dict[str, object] = {
        "source": str(source.resolve()),
        "python": str(python),
        "python_sha256": file_hash(python),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "version": sys.version,
        "library": str(library),
        "library_sha256": file_hash(library),
        "configuration": file_hash(configuration) if configuration.is_file() else None,
        "template": file_hash(template),
        "builder": file_hash(Path(__file__)),
        "compiler": file_hash(Path(compiler)),
        "environment": {
            name: environment.get(name) for name in ("INCLUDE", "LIB", "CL", "_CL_")
        },
    }
    key = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    worktree = development_home(source)
    cache = worktree / "hosts"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / key
    executable = destination / "Scopecat Dev.exe"
    with FileLock(cache / "host.lock"):
        if not destination.exists():
            print("Preparing the Windows source host (requires MSVC).", flush=True)
            with tempfile.TemporaryDirectory(prefix=".host-", dir=cache) as directory:
                staging = Path(directory)
                # UTF-16 code units avoid source/compiler encoding and quoting issues.
                values = {
                    "PYTHON": str(python),
                    "LIBRARY": str(library),
                    "ID": f"org.scopecat.development.{worktree.name}",
                }
                declarations: list[str] = []
                for name, value in values.items():
                    raw = value.encode("utf-16-le")
                    units = [
                        str(int.from_bytes(raw[i : i + 2], "little"))
                        for i in range(0, len(raw), 2)
                    ]
                    declarations.append(
                        f"static wchar_t SCOPECAT_DEV_{name}[] = "
                        f"{{{','.join(units)},0}};"
                    )
                (staging / "host_config.h").write_text("\n".join(declarations))
                built = staging / executable.name
                subprocess.run(  # noqa: S603 - local source and selected compiler
                    [
                        compiler,
                        "/nologo",
                        "/O2",
                        "/MT",
                        str(template),
                        f"/I{staging}",
                        f"/Fe:{built}",
                        f"/Fo:{staging / 'host.obj'}",
                        "shell32.lib",
                        "/link",
                        "/SUBSYSTEM:CONSOLE",
                    ],
                    check=True,
                    env=environment,
                    capture_output=True,
                    timeout=120,
                )
                check_interpreter(built, identity)
                (staging / "host.json").write_text(
                    json.dumps(
                        {
                            "identity": identity,
                            "executable_sha256": file_hash(built),
                        },
                        indent=2,
                    )
                )
                staging.rename(destination)
        receipt = cast(
            "dict[str, object]", json.loads((destination / "host.json").read_text())
        )
        if receipt.get("identity") != identity or receipt.get(
            "executable_sha256"
        ) != file_hash(executable):
            raise ValueError(
                "Development host cache changed; preserve it for diagnosis."
            )
        check_interpreter(executable, identity)
    return executable


def enter_host(source: Path, home: Path) -> None:
    """Reenter before owner locks; preserve the terminal and propagate exit status."""
    marker = os.environ.pop(MARKER, None)
    if marker is not None:
        selected = cast("dict[str, object]", json.loads(marker))
        if (
            selected.get("source") != str(source.resolve())
            or selected.get("python") != str(Path(sys.executable).absolute())
            or selected.get("prefix") != sys.prefix
            or selected.get("host") != str(Path(psutil.Process().exe()).resolve())
        ):
            raise ValueError(
                "Development host identity mismatch; refusing recursive startup."
            )
        return
    executable = build_host(source)
    print(f"Windows source host: {executable}", flush=True)
    environment = clean_environment()
    environment[MARKER] = json.dumps(
        {
            "source": str(source.resolve()),
            "python": str(Path(sys.executable).absolute()),
            "prefix": sys.prefix,
            "host": str(executable.resolve()),
        }
    )
    with subprocess.Popen(  # noqa: S603 - verified locally built host
        [str(executable), "-E", "-m", "scripts.dev", "--home", str(home.resolve())],
        env=environment,
    ) as child:
        while True:
            try:
                raise SystemExit(child.wait())
            except KeyboardInterrupt:
                # Console Ctrl+C reaches both processes; let the host clean up.
                continue
