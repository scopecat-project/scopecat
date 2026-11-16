"""The Mac maintenance console survives relocation without borrowing PATH Python."""

import json
import os
import site
import subprocess
import venv
from pathlib import Path

import pytest
from scripts.app_build.native_package import _write_macos_console

pytestmark = pytest.mark.skipif(os.name == "nt", reason="macOS POSIX console")


type ConsoleRuntime = tuple[Path, dict[str, str]]


@pytest.fixture
def console_runtime(tmp_path: Path) -> ConsoleRuntime:
    staging = tmp_path / ".native-build-original"
    python = staging / "Scopecat.app/Contents/Resources/python"
    venv.EnvBuilder(with_pip=False).create(python)
    library = next((python / "lib").glob("python*/site-packages"))
    # Reuse installed test dependencies while keeping a distinct executable/prefix.
    (library / "test-dependencies.pth").write_text(
        "import site; "
        + "; ".join(f"site.addsitedir({path!r})" for path in site.getsitepackages())
        + "\n"
    )
    old_console = python / "bin/scopecat"
    old_console.write_text(f"#!{python / 'bin/python3'}\nraise SystemExit(99)\n")
    _write_macos_console(python)
    moved = tmp_path / "Moved 中文 application"
    staging.rename(moved)
    assert not staging.exists()
    python = moved / python.relative_to(staging)
    home = tmp_path / "User 中文 home"
    environment = dict(os.environ, PATH="", HOME=str(home), PYTHONPATH="/missing")
    return python, environment


def run_console(
    python: Path, environment: dict[str, str], *arguments: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - disposable test runtime
        [str(python / "bin/scopecat"), *arguments],
        env=environment,
        cwd=python.parent,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_relocated_console_runs_real_help_and_status(
    console_runtime: ConsoleRuntime, tmp_path: Path
):
    python, environment = console_runtime

    def contents():
        return {
            path.relative_to(python): path.read_bytes()
            for path in python.rglob("*")
            if path.is_file()
        }

    before = contents()
    help_result = run_console(python, environment, "--help")
    assert help_result.returncode == 0, help_result.stderr
    assert "scopecat" in help_result.stdout and "app" in help_result.stdout
    home = tmp_path / "Selected 中文 home with spaces"
    result = run_console(python, environment, "app", "--home", str(home))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"state": "not-installed", "home": str(home)}
    invalid = run_console(python, environment, "app", "--unknown-option")
    assert invalid.returncode == 2
    assert not Path(environment["HOME"]).exists()
    assert contents() == before


def test_console_forwards_arguments_identity_and_exit_code(
    console_runtime: ConsoleRuntime,
):
    python, environment = console_runtime
    library = next((python / "lib").glob("python*/site-packages"))
    module = library / "scopecat_app"
    module.mkdir()
    (module / "__init__.py").write_text("")
    (module / "public_cli.py").write_text(
        "import json, sys\n"
        "print(json.dumps([sys.executable, sys.prefix, sys.argv[1:]]))\n"
        "raise SystemExit(23)\n"
    )
    arguments = [
        "",
        "中文 path with spaces",
        "single'quote",
        'double"quote',
        "$HOME",
        "*",
    ]
    result = run_console(python, environment, *arguments)
    assert result.returncode == 23, result.stderr
    executable, prefix, forwarded = json.loads(result.stdout)
    assert Path(executable) == python / "bin/python3"
    assert Path(prefix) == python
    assert forwarded == arguments
    # A missing bundled interpreter must fail, even if a system Python exists.
    (python / "bin/python3").unlink()
    missing = run_console(python, dict(environment, PATH=os.defpath), "--help")
    # macOS /bin/sh reports 126; Linux dash reports 127 for this exec failure.
    assert missing.returncode in (126, 127)
    assert str(python / "bin/python3") in missing.stderr


@pytest.mark.parametrize("trial_mode", [False, True])
def test_build_replaces_console_before_signing_and_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, trial_mode: bool
):
    import tarfile

    from scripts.app_build import (
        cocoa_dependency,
        macos_signing,
        native_package,
        runtime_install,
    )

    source = tmp_path / "delivery"
    (source / "toolchain").mkdir(parents=True)
    (source / "bundle.json").write_text(
        json.dumps({"public_version": "0.2.0", "build_number": 1})
    )
    runtime = tmp_path / "runtime"
    (runtime / "bin").mkdir(parents=True)
    (runtime / "lib").mkdir()
    (runtime / "bin/python3").write_text("runtime")
    (runtime / "lib/libpython3.14.dylib").write_text("library")
    if trial_mode:
        core = runtime / "lib/python3.14/site-packages/scopecat"
        core.mkdir(parents=True)
        (core / "__init__.py").write_text("")
    with tarfile.open(source / "toolchain/python.tar", "w") as archive:
        archive.add(runtime / "bin", arcname="bin")
        archive.add(runtime / "lib", arcname="lib")
    monkeypatch.setattr(native_package.sys, "platform", "darwin")

    def resolve_delivery(path: Path):
        return path

    monkeypatch.setattr(native_package, "resolve_delivery", resolve_delivery)

    def verify_bundle_stub(_: Path) -> None:
        return None

    monkeypatch.setattr(native_package, "verify_bundle", verify_bundle_stub)

    def verify_runtime_bundle_stub(_: Path) -> None:
        return None

    monkeypatch.setattr(runtime_install, "verify_bundle", verify_runtime_bundle_stub)
    import shutil

    def author_inputs(root: Path, _: object):
        return root

    monkeypatch.setattr(native_package, "native_author_inputs", author_inputs)

    def copy_resources(src: Path, dst: Path, **_kw: object):
        return Path(shutil.copytree(src, dst))

    monkeypatch.setattr(
        native_package,
        "build_author_resources",
        copy_resources,
    )

    def verify_resources_stub(_: Path) -> None:
        return None

    monkeypatch.setattr(
        native_package.author_resources, "verify_resources", verify_resources_stub
    )

    def verify_wheel_stub(_: Path) -> None:
        return None

    monkeypatch.setattr(cocoa_dependency, "verify_wheel", verify_wheel_stub)
    installed: list[Path] = []

    def run(command: list[str], **_kwargs: object):
        if command[1:3] == ["pip", "install"]:
            assert {"--offline", "--no-index", "--no-deps", "--require-hashes"} <= set(
                command
            )
            python = Path(command[command.index("--python") + 1])
            entry = python.with_name("scopecat")
            entry.write_text(f"#!{python}\n")
            installed.append(entry)

    signed: list[Path] = []

    def sign(app: Path):
        entry = app / "Contents/Resources/python/bin/scopecat"
        assert entry == installed[0]
        assert entry.read_text().startswith("#!/bin/sh\n")
        assert ".native-build-" not in entry.read_text()
        assert entry.stat().st_mode & 0o111 == 0o111
        if trial_mode:
            from scripts.app_build.trial_package import inspect

            receipt = inspect(app)
            assert receipt["id"] == "qa-build"
            assert receipt["home"] == str(tmp_path / "trial-home")
        signed.append(app)

    monkeypatch.setattr(native_package, "_run", run)
    monkeypatch.setattr(runtime_install.subprocess, "run", run)
    monkeypatch.setattr(macos_signing, "sign", sign)
    destination = tmp_path / "Final 中文 Scopecat.app"
    from scripts.app_build.trial_package import TrialBuild

    trial = (
        TrialBuild(tmp_path / "trial-home", tmp_path / "trial-home/locator", "qa-build")
        if trial_mode
        else None
    )
    assert native_package.build(source, destination, trial=trial) == destination
    assert not (tmp_path / "trial-home").exists()
    assert len(signed) == 1 and not signed[0].exists()
    assert not installed[0].exists()
    assert (destination / "Contents/Resources/python/bin/scopecat").is_file()


@pytest.mark.parametrize(
    ("filename", "comparison", "result"),
    [
        ("native_launcher.m", '!strcmp(argv[i], "--trial-window-probe")', "YES"),
        ("native_launcher.c", '!wcscmp(argv[i], L"--trial-window-probe")', "TRUE"),
    ],
)
def test_trial_probe_launcher_suppresses_only_modal(
    filename: str, comparison: str, result: str
):
    """Both platform launchers keep nonzero failures, without a trial probe modal."""
    from scripts.app_build import native_package

    source = Path(native_package.__file__).with_name(filename).read_text()
    guard = (
        "#ifdef SCOPECAT_TRIAL\n"
        "        // Explicit one-shot verification must return failures "
        "without a modal.\n"
        f"        if ({comparison}) check = {result};\n"
        "#endif"
    )
    assert guard in source
    # Release compilation excludes the sole probe-specific behavior; ordinary
    # trial launches never set this flag. Python still validates the probe mode.
    assert "--trial-window-probe" not in source.replace(guard, "")
    assert "if (result && !check)" in source
    assert "return result;" in source or "return (int)result;" in source
