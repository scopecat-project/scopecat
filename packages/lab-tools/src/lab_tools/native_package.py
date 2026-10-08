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
from .bundle import MANIFEST, resolve_delivery, verify_bundle
from .release_identity import ReleaseIdentity


def _run(command: list[str]) -> None:
    _ = subprocess.run(command, check=True)  # noqa: S603 - fixed build tools


def native_identity(source: Path) -> ReleaseIdentity:
    document = cast("dict[str, object]", json.loads((source / MANIFEST).read_text()))
    version = document.get("public_version")
    number = document.get("build_number")
    if not isinstance(version, str) or type(number) is not int:
        raise ValueError("Native builds require public_version and build_number")
    return ReleaseIdentity(version, number)


def _write_macos_console(python_home: Path) -> None:
    # Wheel installers embed the temporary build interpreter in console scripts.
    # Replace only our public entry before signing; resolve Python beside this
    # script on every invocation, without PATH lookup or writes to the app.
    console = python_home / "bin/scopecat"
    _ = console.write_text(
        '#!/bin/sh\nexec "${0%/*}/python3" -I -B -m lab_tools.public_cli "$@"\n',
        encoding="utf-8",
    )
    console.chmod(0o755)


def build(source: Path, destination: Path) -> Path:
    if sys.platform not in ("darwin", "win32"):
        raise ValueError("原生应用需要在 macOS 或 Windows 上构建")
    source = resolve_delivery(source)
    _ = verify_bundle(source)
    identity = native_identity(source)
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
        _ = verify_bundle(payload)
        if sys.platform == "darwin":
            from .cocoa_dependency import verify_wheel

            verify_wheel(payload / "wheels")
        if sys.platform == "win32":
            from .windows_dependency import verify_wheel as verify_windows_wheel

            verify_windows_wheel(payload / "wheels")
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
        if sys.platform == "darwin":
            _write_macos_console(python_home)
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
            _ = shutil.copyfile(
                Path(__file__).with_name("icons") / "Scopecat.icns",
                resources / "Scopecat.icns",
            )
            executable = app / "Contents/MacOS/Scopecat"
            executable.parent.mkdir(parents=True)
            (python_library,) = (python_home / "lib").glob("libpython3.*.dylib")
            # The extracted runtime can carry its build-time absolute install
            # name. Link our host through the app-relative library path instead.
            _run(
                [
                    "/usr/bin/install_name_tool",
                    "-id",
                    f"@rpath/{python_library.name}",
                    str(python_library),
                ]
            )
            _run(
                [
                    "/usr/bin/clang",
                    "-fobjc-arc",
                    "-framework",
                    "Cocoa",
                    str(Path(__file__).with_name("native_launcher.m")),
                    str(python_library),
                    "-Wl,-rpath,@executable_path/../Resources/python/lib",
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
                        "CFBundleIconFile": "Scopecat.icns",
                        "CFBundlePackageType": "APPL",
                        "CFBundleShortVersionString": identity.short_version,
                        "CFBundleVersion": str(identity.build_number),
                        "ScopecatReleaseVersion": identity.version,
                        "NSHighResolutionCapable": True,
                    },
                    stream,
                )
        else:
            icon = Path(__file__).with_name("icons") / "Scopecat.ico"
            resource = Path(temporary) / "icon.rc"
            resource.write_text(f'1 ICON "{icon.as_posix()}"\n', encoding="utf-8")
            compiled_resource = Path(temporary) / "icon.res"
            _run(
                [
                    "rc.exe",
                    "/nologo",
                    "/c65001",
                    f"/fo{compiled_resource}",
                    str(resource),
                ]
            )
            _run(
                [
                    "cl.exe",
                    "/nologo",
                    "/utf-8",
                    "/O2",
                    "/MT",
                    str(Path(__file__).with_name("native_launcher.c")),
                    str(compiled_resource),
                    f"/Fe:{app / 'Scopecat.exe'}",
                    f"/Fo:{Path(temporary) / 'launcher.obj'}",
                    "shell32.lib",
                    "user32.lib",
                    "/link",
                    "/SUBSYSTEM:WINDOWS",
                ]
            )
        _ = verify_bundle(payload)
        if sys.platform == "darwin":
            from .macos_signing import sign

            sign(app)
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
            from .macos_signing import verify

            verify(app)
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
            identity = native_identity(app / "resources/payload")
            script = staging / "setup.iss"
            _ = script.write_text(
                "[Setup]\nAppId=org.scopecat.desktop\nAppName=Scopecat\n"
                f"AppVersion={identity.version}\n"
                f"VersionInfoVersion={identity.windows_version}\n"
                "DefaultDirName={userpf}\\Scopecat\n"
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
    installer: Path | None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--installer", type=Path, help="Also create a DMG or Windows Setup"
    )
    args = cast("Arguments", cast("object", parser.parse_args()))
    app = build(args.source, args.destination)
    print(app)
    if args.installer:
        print(package(app, args.installer))


if __name__ == "__main__":
    main()
