"""A cached macOS bundle identity around the current editable Python environment.

This is a source host, not an installable application. Its only payload is the
launcher and existing icon; Python, dependencies and source remain where they are.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import plistlib
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import cast

import psutil
from filelock import FileLock

from .bundle import file_hash
from .dev_resources import development_home

MARKER = "SCOPECAT_DEV_NATIVE_HOST"
NAME = "Scopecat Dev"
EXECUTABLE = "scopecat-dev"


def shared_library() -> Path:
    """Use this interpreter's shared library, including framework Python layouts."""
    library = cast("object", sysconfig.get_config_var("LDLIBRARY"))
    directory = cast("object", sysconfig.get_config_var("LIBDIR"))
    candidates: list[Path] = []
    if isinstance(library, str) and isinstance(directory, str):
        candidates.append(Path(directory) / library)
    framework = cast("object", sysconfig.get_config_var("PYTHONFRAMEWORK"))
    prefix = cast("object", sysconfig.get_config_var("PYTHONFRAMEWORKPREFIX"))
    version = cast("object", sysconfig.get_config_var("VERSION"))
    if all(isinstance(value, str) and value for value in (framework, prefix, version)):
        candidates.append(
            Path(str(prefix))
            / f"{framework}.framework"
            / "Versions"
            / str(version)
            / str(framework)
        )
    for candidate in candidates:
        if candidate.is_file() and candidate.suffix != ".a":
            return candidate.resolve()
    raise ValueError(
        "The development host needs this Python's shared libpython. "
        "Use a shared-library Python environment with uv, then retry."
    )


def host_identity(source: Path) -> dict[str, object]:
    """Only host inputs invalidate the bundle; application source is editable."""
    python = Path(sys.executable).absolute()  # Do not resolve away the venv.
    library = shared_library()
    template = Path(__file__).with_suffix(".c")
    icon = Path(__file__).with_name("icons") / "Scopecat.icns"
    configuration = Path(sys.prefix) / "pyvenv.cfg"
    try:
        compiler = subprocess.check_output(
            ["/usr/bin/clang", "--version"], text=True, stderr=subprocess.STDOUT
        ).strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ValueError(
            "The macOS source host requires Xcode command line tools. "
            "Install them with xcode-select --install, then retry."
        ) from error
    return {
        "format": 1,
        "source": str(source.resolve()),
        "python": str(python),
        "resolved_python": str(python.resolve()),
        "python_sha256": file_hash(python),
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "version": sys.version,
        "abi": sysconfig.get_config_var("SOABI"),
        "machine": platform.machine(),
        "venv_configuration": file_hash(configuration)
        if configuration.is_file()
        else None,
        "library": str(library),
        "library_sha256": file_hash(library),
        "template_sha256": file_hash(template),
        "builder_sha256": file_hash(Path(__file__)),
        "icon_sha256": file_hash(icon),
        "compiler": compiler,
        "build_environment": {
            name: os.environ.get(name)
            for name in ("SDKROOT", "MACOSX_DEPLOYMENT_TARGET", "DEVELOPER_DIR")
        },
    }


def clean_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in ("PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__", MARKER):
        environment.pop(name, None)
    return environment


def check_interpreter(executable: Path, identity: dict[str, object]) -> None:
    """Fail before owning an application if the host escapes its selected venv."""
    result = subprocess.run(  # noqa: S603 - locally built checked host, fixed probe
        [
            str(executable),
            "-I",
            "-c",
            (
                "import json,sys;"
                "print(json.dumps([sys.executable,sys.prefix,sys.base_prefix]))"
            ),
        ],
        env=clean_environment(),
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    expected = [identity["python"], identity["prefix"], identity["base_prefix"]]
    try:
        observed = cast("object", json.loads(result.stdout))
        matches = observed == expected
    except json.JSONDecodeError:
        matches = False
    if result.returncode or not matches:
        raise ValueError(
            "Development host did not preserve the selected Python environment. "
            f"No application was started. {result.stderr[-4096:]}"
        )


def build_host(source: Path) -> Path:
    """Build atomically and retain old hosts; never mutate a running executable."""
    identity = host_identity(source)
    encoded = json.dumps(identity, sort_keys=True).encode()
    key = hashlib.sha256(encoded).hexdigest()
    worktree = development_home(source)
    cache = worktree / "hosts"
    cache.mkdir(parents=True, exist_ok=True)
    destination = cache / key
    app = destination / f"{NAME}.app"
    executable = app / "Contents/MacOS" / EXECUTABLE
    with FileLock(cache / "host.lock"):
        if not destination.exists():
            print(
                "Preparing the macOS source host (requires Xcode command line tools).",
                flush=True,
            )
            with tempfile.TemporaryDirectory(prefix=".host-", dir=cache) as directory:
                staging = Path(directory)
                built = staging / f"{NAME}.app"
                contents = built / "Contents"
                native = contents / "MacOS" / EXECUTABLE
                native.parent.mkdir(parents=True)
                resources = contents / "Resources"
                resources.mkdir()
                shutil.copyfile(
                    Path(__file__).with_name("icons") / "Scopecat.icns",
                    resources / "Scopecat.icns",
                )
                with (contents / "Info.plist").open("wb") as stream:
                    plistlib.dump(
                        {
                            "CFBundleIdentifier": (
                                f"org.scopecat.development.{worktree.name}"
                            ),
                            "CFBundleName": NAME,
                            "CFBundleDisplayName": NAME,
                            "CFBundleExecutable": EXECUTABLE,
                            "CFBundlePackageType": "APPL",
                            "CFBundleIconFile": "Scopecat.icns",
                            "CFBundleShortVersionString": "0.0.0",
                            "CFBundleVersion": "1",
                            "NSHighResolutionCapable": True,
                        },
                        stream,
                    )
                # Hex bytes safely handle spaces, quotes and non-ASCII venv paths.
                literal = (
                    '"'
                    + "".join(
                        f"\\x{byte:02x}"
                        for byte in os.fsencode(str(identity["python"]))
                    )
                    + '"'
                )
                library = Path(str(identity["library"]))
                subprocess.run(  # noqa: S603 - fixed compiler and checked inputs
                    [
                        "/usr/bin/clang",
                        f"-DSCOPECAT_DEV_PYTHON={literal}",
                        str(Path(__file__).with_suffix(".c")),
                        str(library),
                        "-Xlinker",
                        "-rpath",
                        "-Xlinker",
                        str(library.parent),
                        "-o",
                        str(native),
                    ],
                    check=True,
                )
                # Both the icon and Info.plist must be present before the seal.
                subprocess.run(  # noqa: S603 - fixed tool, source-only owned bundle
                    [
                        "/usr/bin/codesign",
                        "--force",
                        "--sign",
                        "-",
                        "--timestamp=none",
                        str(built),
                    ],
                    check=True,
                )
                check_interpreter(native, identity)
                if host_identity(source) != identity:
                    raise ValueError(
                        "Host inputs changed during preparation; retry startup."
                    )
                receipt = {
                    "identity": identity,
                    "files": {
                        path.relative_to(staging).as_posix(): file_hash(path)
                        for path in built.rglob("*")
                        if path.is_file()
                    },
                }
                (staging / "host.json").write_text(json.dumps(receipt, indent=2))
                staging.rename(destination)
        receipt = cast(
            "dict[str, object]", json.loads((destination / "host.json").read_text())
        )
        actual = {
            path.relative_to(destination).as_posix(): file_hash(path)
            for path in app.rglob("*")
            if path.is_file()
        }
        if receipt.get("identity") != identity or receipt.get("files") != actual:
            raise ValueError(
                "Development host cache changed; preserve it for diagnosis."
            )
        subprocess.run(  # noqa: S603 - verify the final cached path before execution
            ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
            check=True,
        )
        check_interpreter(executable, identity)
    return executable


def enter_host(source: Path, home: Path) -> None:
    """Replace the launcher before acquiring owner locks; inherit terminal and PID."""
    marker = os.environ.get(MARKER)
    if marker is not None:
        selected = cast("dict[str, object]", json.loads(marker))
        if selected.get("pid") == os.getpid():
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
    print(f"macOS source host: {executable.parents[2]}", flush=True)
    environment = clean_environment()
    environment[MARKER] = json.dumps(
        {
            "pid": os.getpid(),
            "source": str(source.resolve()),
            "python": str(Path(sys.executable).absolute()),
            "prefix": sys.prefix,
            "host": str(executable.resolve()),
        }
    )
    # In-process Py_BytesMain keeps this PID, stdin, signals and exit status. Passing
    # an explicit home also avoids allocating a second --temporary home on entry.
    os.execve(  # noqa: S606 - verified locally built host, preserve terminal/PID
        executable,
        [str(executable), "-m", "lab_tools.dev", "--home", str(home.resolve())],
        environment,
    )
