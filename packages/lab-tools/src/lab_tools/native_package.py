"""Build a relocatable native application from a locked platform delivery."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Protocol, cast

from . import toolchain
from .bundle import MANIFEST, file_hash, resolve_delivery, verify_bundle


def _run(command: list[str]) -> None:
    _ = subprocess.run(command, check=True)  # noqa: S603 - fixed build tools


def build(source: Path, destination: Path, *, initializer: Path | None = None) -> Path:
    if sys.platform not in ("darwin", "win32"):
        raise ValueError("原生应用需要在 macOS 或 Windows 上构建")
    source = resolve_delivery(source)
    _ = verify_bundle(source)
    destination = destination.absolute()
    if destination.exists():
        raise FileExistsError(destination)
    if destination.is_relative_to(source):
        raise ValueError("应用输出目录不能位于源交付目录内")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".native-build-", dir=destination.parent
    ) as temporary:
        app = (
            Path(temporary) / "Scopecat.app"
            if sys.platform == "darwin"
            else Path(temporary) / "Scopecat"
        )
        resources = app / (
            "Contents/Resources" if sys.platform == "darwin" else "resources"
        )
        resources.mkdir(parents=True)
        payload = resources / "payload"
        if (source / "toolchain/python.tar").is_file():
            _ = shutil.copytree(source, payload)
        else:
            _ = toolchain.build(source, payload)
        document = verify_bundle(payload)
        if initializer:
            _ = shutil.copyfile(initializer, payload / "initialize.py")
            document["files"]["initialize.py"] = file_hash(payload / "initialize.py")
            _ = (payload / MANIFEST).write_text(
                json.dumps(document, indent=2) + "\n", encoding="utf-8"
            )
        python_home = resources / "python"
        with tarfile.open(payload / "toolchain/python.tar") as archive:
            archive.extractall(python_home, filter="data")
        python = python_home / ("python.exe" if os.name == "nt" else "bin/python3")
        # The installed application owns its desktop host. First launch must not
        # install a GUI or borrow a host from a previously selected environment.
        _run(
            [
                str(payload / "toolchain" / ("uv.exe" if os.name == "nt" else "uv")),
                "pip",
                "install",
                "--python",
                str(python),
                "--break-system-packages",
                "--offline",
                "--no-index",
                "--no-deps",
                "--require-hashes",
                "--find-links",
                str(payload / "wheels"),
                "-r",
                str(payload / "requirements.lock"),
            ]
        )
        _ = (resources / "bootstrap.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "root = Path(__file__).resolve().parent\n"
            "entry = root.parents[1] if sys.platform == 'darwin' "
            "else root.parent / 'Scopecat.exe'\n"
            "sys.argv[1:1] = ['--payload', str(root / 'payload'), "
            "'--entry', str(entry)]\n"
            "from lab_tools.native_bootstrap import main\nmain()\n",
            encoding="utf-8",
        )
        if sys.platform == "darwin":
            executable = app / "Contents/MacOS/Scopecat"
            executable.parent.mkdir(parents=True)
            _run(
                [
                    "/usr/bin/clang",
                    "-fobjc-arc",
                    "-framework",
                    "Cocoa",
                    str(Path(__file__).with_name("native_launcher.m")),
                    "-o",
                    str(executable),
                ]
            )
            with (app / "Contents/Info.plist").open("wb") as stream:
                plistlib.dump(
                    {
                        "CFBundleIdentifier": "org.scopecat.desktop",
                        "CFBundleName": "Scopecat",
                        "CFBundleExecutable": "Scopecat",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": "0.2.0",
                        "CFBundleVersion": "1",
                        "NSHighResolutionCapable": True,
                    },
                    stream,
                )
        else:
            _run(
                [
                    "cl.exe",
                    "/nologo",
                    "/utf-8",
                    "/O2",
                    "/MT",
                    str(Path(__file__).with_name("native_launcher.c")),
                    f"/Fe:{app / 'Scopecat.exe'}",
                    f"/Fo:{Path(temporary) / 'launcher.obj'}",
                    "shell32.lib",
                    "user32.lib",
                    "/link",
                    "/SUBSYSTEM:WINDOWS",
                ]
            )
        _ = verify_bundle(payload)
        _ = app.rename(destination)
    return destination


def package(app: Path, destination: Path) -> Path:
    """Wrap a completed app for native installation; never launch it."""
    app = app.resolve()
    destination = destination.absolute()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".native-installer-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        if sys.platform == "darwin":
            contents = staging / "contents"
            contents.mkdir()
            _ = shutil.copytree(app, contents / "Scopecat.app")
            (contents / "Applications").symlink_to(
                "/Applications", target_is_directory=True
            )
            _run(
                [
                    "/usr/bin/hdiutil",
                    "create",
                    "-volname",
                    "Scopecat",
                    "-srcfolder",
                    str(contents),
                    "-format",
                    "UDZO",
                    str(destination),
                ]
            )
        elif sys.platform == "win32":
            compiler = shutil.which("ISCC.exe")
            if compiler is None:
                candidate = (
                    Path(os.environ["PROGRAMFILES(X86)"]) / "Inno Setup 6/ISCC.exe"
                )
                if not candidate.is_file():
                    raise ValueError("构建 Windows 安装程序需要 Inno Setup 6")
                compiler = str(candidate)
            script = staging / "setup.iss"
            _ = script.write_text(
                "[Setup]\nAppId=org.scopecat.desktop\nAppName=Scopecat\n"
                "AppVersion=0.2.0\nDefaultDirName={userpf}\\Scopecat\n"
                "PrivilegesRequired=lowest\nUninstallDisplayIcon={app}\\Scopecat.exe\n"
                f"OutputDir={staging}\nOutputBaseFilename=Scopecat-Setup\n"
                "Compression=lzma2\nSolidCompression=yes\nDisableProgramGroupPage=yes\n"
                "[Files]\n"
                f'Source: "{app}\\*"; DestDir: "{{app}}"; '
                "Flags: ignoreversion recursesubdirs createallsubdirs\n"
                "[Icons]\n"
                'Name: "{userprograms}\\Scopecat"; Filename: "{app}\\Scopecat.exe"\n',
                encoding="utf-8-sig",
            )
            _run([compiler, str(script)])
            _ = (staging / "Scopecat-Setup.exe").rename(destination)
        else:
            raise ValueError("原生安装程序需要在 macOS 或 Windows 上构建")
    return destination


class Arguments(Protocol):
    source: Path
    destination: Path
    initializer: Path | None
    installer: Path | None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--initializer", type=Path)
    parser.add_argument(
        "--installer", type=Path, help="Also create a DMG or Windows Setup"
    )
    args = cast("Arguments", cast("object", parser.parse_args()))
    app = build(args.source, args.destination, initializer=args.initializer)
    print(app)
    if args.installer:
        print(package(app, args.installer))


if __name__ == "__main__":
    main()
