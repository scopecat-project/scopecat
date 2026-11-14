"""Build a relocatable native application from a locked platform delivery."""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from importlib.resources import files
from pathlib import Path
from typing import Protocol, cast

from scripts.app_build import toolchain
from scripts.app_build.author_resources import build as build_author_resources
from scripts.app_build.author_resources import native_author_inputs
from scripts.app_build.release_identity import ReleaseIdentity
from scripts.app_build.runtime_install import install_runtime
from scripts.app_build.trial_package import (
    TrialBuild,
    install_policy,
    refuse_automatic_verification,
)

from scopecat_app import author_resources
from scopecat_app.bundle import MANIFEST, resolve_delivery, verify_bundle


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
        '#!/bin/sh\nexec "${0%/*}/python3" -I -B -m scopecat_app.public_cli "$@"\n',
        encoding="utf-8",
    )
    console.chmod(0o755)


def build(source: Path, destination: Path, *, trial: TrialBuild | None = None) -> Path:
    if sys.platform not in ("darwin", "win32"):
        raise ValueError("原生应用需要在 macOS 或 Windows 上构建")
    if trial is not None:
        trial.validate()
    source = resolve_delivery(source)
    source_document = verify_bundle(source)
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
        payload = Path(temporary) / "delivery"
        if (source / "toolchain/python.tar").is_file():
            _ = shutil.copytree(source, payload)
        else:
            _ = toolchain.build(source, payload)
        document = verify_bundle(payload)
        if document != source_document:
            raise ValueError(
                "Native staged delivery identity changed during preparation"
            )
        inputs = native_author_inputs(payload, document)
        if sys.platform == "darwin":
            from scripts.app_build.cocoa_dependency import verify_wheel

            verify_wheel(payload / "wheels")
        if sys.platform == "win32":
            from scripts.app_build.windows_dependency import (
                verify_wheel as verify_windows_wheel,
            )

            verify_windows_wheel(payload / "wheels")
        python_home = resources / "python"
        python = install_runtime(payload, python_home)
        if verify_bundle(payload) != document:
            raise ValueError(
                "Native delivery identity changed during runtime installation"
            )
        payload = build_author_resources(inputs, resources / "payload", python=python)
        if sys.platform == "darwin":
            _write_macos_console(python_home)
        digest = install_policy(resources, trial) if trial is not None else None
        main_call = f"main(expected_trial_digest={digest!r})" if digest else "main()"
        _ = (resources / "bootstrap.py").write_text(
            "import sys\nfrom pathlib import Path\n"
            "root = Path(__file__).resolve().parent\n"
            "entry = root.parents[1] if sys.platform == 'darwin' "
            "else root.parent / 'Scopecat.exe'\n"
            "sys.argv[1:1] = ['--payload', str(root / 'payload'), "
            "'--entry', str(entry)]\n"
            "from scopecat_app.native_bootstrap import main\n" + main_call + "\n",
            encoding="utf-8",
        )
        if sys.platform == "darwin":
            _ = shutil.copyfile(
                Path(str(files("scopecat_app").joinpath("icons"))) / "Scopecat.icns",
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
                    *(["-DSCOPECAT_TRIAL=1"] if trial is not None else []),
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
                        "CFBundleIdentifier": f"org.scopecat.trial.{trial.identity}"
                        if trial
                        else "org.scopecat.desktop",
                        "CFBundleName": f"Scopecat Trial {trial.identity}"
                        if trial
                        else "Scopecat",
                        "CFBundleDisplayName": f"Scopecat Trial {trial.identity}"
                        if trial
                        else "Scopecat",
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
            icon = Path(str(files("scopecat_app").joinpath("icons"))) / "Scopecat.ico"
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
                    *(
                        [
                            "/DSCOPECAT_TRIAL=1",
                            f'/DSCOPECAT_APP_ID=L"org.scopecat.trial.{trial.identity}"',
                        ]
                        if trial
                        else []
                    ),
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
        _ = author_resources.verify_resources(payload)
        if sys.platform == "darwin":
            from scripts.app_build.macos_signing import sign

            sign(app)
        _ = app.rename(destination)
    return destination


def package(app: Path, destination: Path) -> Path:
    """Wrap a completed app for native installation; never launch it."""
    app = app.resolve()
    refuse_automatic_verification(app)
    destination = destination.absolute()
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=".native-installer-", dir=destination.parent
    ) as temporary:
        staging = Path(temporary)
        if sys.platform == "darwin":
            from scripts.app_build.macos_signing import verify

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
                "CloseApplications=no\nRestartApplications=no\n"
                "[Files]\n"
                f'Source: "{app}\\*"; DestDir: "{{app}}"; '
                "Flags: ignoreversion recursesubdirs createallsubdirs\n"
                "[Icons]\n"
                'Name: "{userprograms}\\Scopecat"; Filename: "{app}\\Scopecat.exe"; '
                'AppUserModelID: "org.scopecat.desktop"\n'
                + Path(__file__)
                .with_name("windows_upgrade.iss")
                .read_text(encoding="utf-8"),
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
    trial_home: Path | None
    trial_locator: Path | None
    trial_id: str | None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument(
        "--installer", type=Path, help="Also create a DMG or Windows Setup"
    )
    parser.add_argument("--trial-home", type=Path)
    parser.add_argument("--trial-locator", type=Path)
    parser.add_argument("--trial-id")
    args = cast("Arguments", cast("object", parser.parse_args()))
    trial = None
    if any(
        value is not None
        for value in (args.trial_home, args.trial_locator, args.trial_id)
    ):
        if (
            args.trial_home is None
            or args.trial_locator is None
            or args.trial_id is None
            or args.installer is not None
        ):
            parser.error(
                "Trial builds require home, locator and ID; "
                "installers are not supported"
            )
        trial = TrialBuild(args.trial_home, args.trial_locator, args.trial_id)
    app = build(args.source, args.destination, trial=trial)
    print(app)
    if args.installer:
        print(package(app, args.installer))


if __name__ == "__main__":
    main()
