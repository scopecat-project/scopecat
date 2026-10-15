"""Offline release rehearsal checks completeness and immutable provenance."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import textwrap
import zipfile
from pathlib import Path
from typing import Any

import pytest

from lab_tools import author_resources
from lab_tools.bundle import file_hash, inventory, target_identity

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/release_assets.py"
spec = importlib.util.spec_from_file_location("release_assets", SCRIPT)
assert spec is not None and spec.loader is not None
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)
native_spec = importlib.util.spec_from_file_location(
    "verify_native_application", SCRIPT.with_name("verify_native_application.py")
)
assert native_spec is not None and native_spec.loader is not None
native_producer = importlib.util.module_from_spec(native_spec)
native_spec.loader.exec_module(native_producer)
SHA = "a" * 40


@pytest.mark.parametrize("platform", ["windows", "macos"])
@pytest.mark.parametrize("problem", [None, "missing", "empty", "unset-platform"])
def test_workflow_stages_native_manifests(
    tmp_path: Path, platform: str, problem: str | None
) -> None:
    """Execute the upload prerequisite with MSVC's mixed-case environment key."""
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("Workflow staging requires Bash")
    workflow = (SCRIPT.parents[1] / ".github/workflows/release-publish.yml").read_text()
    step = workflow.split("      - name: Stage native release manifests\n", 1)[1]
    step = step.split("      - uses:", 1)[0]
    variable = next(
        line.strip().split(":", 1)[0]
        for line in step.splitlines()
        if line.endswith(": ${{ matrix.platform }}")
    )
    command = textwrap.dedent(step.split("        run: |\n", 1)[1])
    source = tmp_path / "native-acceptance"
    destination = tmp_path / "release-native"
    source.mkdir()
    destination.mkdir()
    (source / "native-release.json").write_text('{"installer":"fixture"}\n')
    if problem != "missing":
        (source / "bundle.json").write_text("" if problem == "empty" else "{}\n")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() not in {"PLATFORM", "SCOPECAT_RELEASE_PLATFORM"}
    }
    environment.update(Platform="x64", RUNNER_TEMP=tmp_path.as_posix())
    if problem != "unset-platform":
        # Windows updates an existing key without changing its original casing.
        key = next(
            (key for key in environment if key.casefold() == variable.casefold()),
            variable,
        )
        environment[key] = platform
    result = subprocess.run(  # noqa: S603 -- repository-owned workflow, isolated fixtures
        [bash, "--noprofile", "--norc", "-eo", "pipefail", "-c", command],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    if problem is not None:
        assert result.returncode != 0
        return
    assert result.returncode == 0, result.stderr
    assert {path.name for path in destination.iterdir()} == {
        f"{platform}-native-release.json",
        f"{platform}-bundle.json",
    }
    for name in ("native-release.json", "bundle.json"):
        assert (destination / f"{platform}-{name}").read_bytes() == (
            source / name
        ).read_bytes()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


@pytest.fixture
def release(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "assets"
    root.mkdir()
    config = tmp_path / "release.toml"
    config.write_text('version = "0.3.0-rc.1"\nbuild_number = 9\n')
    version = f"0.1.0.dev1770000000+scopecat.0.3.0rc1.g{SHA[:12]}"
    files = {}
    for name in assets.PACKAGES:
        wheel = root / f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as archive:
            archive.writestr(
                f"{name}.dist-info/METADATA", f"Name: {name}\nVersion: {version}\n"
            )
        files[wheel.name] = assets._hash(wheel)
    (root / "scopecat-ui.zip").write_bytes(b"ui archive")
    files["scopecat-ui.zip"] = assets._hash(root / "scopecat-ui.zip")
    write_json(
        root / "preview.json",
        {
            "format": 1,
            "commit": SHA,
            "release_version": "0.3.0-rc.1",
            "build_number": 9,
            "channel": "release",
            "ui_version": f"0.1.0-scopecat.0.3.0.rc.1.g{SHA[:12]}",
            "packages": dict.fromkeys(assets.PACKAGES, version),
            "files": files,
        },
    )
    for platform, (target, suffix) in assets.NATIVE.items():
        installer = root / f"Scopecat-{platform}{suffix}"
        installer.write_bytes(platform.encode())
        bundle = {
            "format": 1,
            "release_version": "0.3.0-rc.1",
            "build_number": 9,
            "target": target_identity(),
            "sources": {"public": SHA},
            "runtime": {
                module: {
                    "distribution": distribution,
                    "version": version,
                    "content_hash": "b" * 64,
                }
                for module, distribution in {
                    "scopecat": "scopecat",
                    "scopecat_server": "scopecat-server",
                    "lab_teaching": "scopecat-lab-teaching",
                }.items()
            },
            "build_id": "identity",
            "files": {
                f"wheels/{name}": digest
                for name, digest in files.items()
                if name.endswith(".whl")
            },
        }
        # Feed a verified full delivery to the real native payload producer.
        source = tmp_path / platform / "delivery"
        for folder in ("gui", "wheels", "toolchain"):
            (source / folder).mkdir(parents=True)
        (source / "gui/index.html").write_text("public UI", encoding="utf-8")
        for wheel in root.glob("*.whl"):
            shutil.copy2(wheel, source / "wheels" / wheel.name)
        (source / "wheels/numpy-1-py3-none-any.whl").write_bytes(b"third party")
        (source / "toolchain/python.tar").write_bytes(b"build-time interpreter")
        bundle["files"] = inventory(source, ("gui", "wheels", "toolchain"))
        for name in (
            "requirements.lock",
            "dependencies.lock",
            "build.lock",
            "install.py",
        ):
            (source / name).write_text("", encoding="utf-8")
            bundle["files"][name] = file_hash(source / name)
        write_json(source / "bundle.json", bundle)
        resources = tmp_path / platform / "resources"
        resources.mkdir()
        payload = author_resources.build(source, resources / "payload")
        projected = json.loads((payload / "bundle.json").read_bytes())
        # Platform declarations are simulated; projection and report writing are real.
        projected["target"]["platform"] = target
        write_json(payload / "bundle.json", projected)
        reports = tmp_path / platform / "reports"
        reports.mkdir()
        native_producer.write_release_manifest(resources, reports, installer)
        for name in ("bundle", "native-release"):
            shutil.copy2(reports / f"{name}.json", root / f"{platform}-{name}.json")
    return root, config


def test_offline_assemble_verify_and_no_overwrite(release: tuple[Path, Path]) -> None:
    root, config = release
    result = assets.assemble(root, SHA, config)
    assert assets.verify(root, SHA, config) == result
    document = json.loads(result.read_text())
    assert document["version"] == "0.3.0-rc.1"
    assert len(document["files"]) == 15
    with pytest.raises(FileExistsError, match="never overwrite"):
        assets.assemble(root, SHA, config)


@pytest.mark.parametrize(
    "name", ["scopecat-ui.zip", "Scopecat-macos.dmg", "windows-bundle.json"]
)
def test_missing_assets_fail(release: tuple[Path, Path], name: str) -> None:
    root, config = release
    (root / name).unlink()
    with pytest.raises(ValueError, match="Missing asset"):
        assets.assemble(root, SHA, config)
    assert not (root / "release.json").exists()


def test_wrong_commit_and_corrupt_hash_fail(release: tuple[Path, Path]) -> None:
    root, config = release
    with pytest.raises(ValueError, match="identity mismatch"):
        assets.assemble(root, "b" * 40, config)
    (root / "Scopecat-windows.exe").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        assets.assemble(root, SHA, config)


@pytest.mark.parametrize(
    "field,value",
    [("release_version", "0.4.0"), ("build_number", 10), ("channel", "preview")],
)
def test_wrong_preview_identity_fails(
    release: tuple[Path, Path], field: str, value: object
) -> None:
    root, config = release
    path = root / "preview.json"
    document = json.loads(path.read_text())
    document[field] = value
    write_json(path, document)
    with pytest.raises(ValueError, match="identity mismatch"):
        assets.assemble(root, SHA, config)


@pytest.mark.parametrize(
    "name", ["../escape.whl", "C:escape.whl", "nested/escape.whl", "nested\\escape.whl"]
)
def test_path_escape_fails(release: tuple[Path, Path], name: str) -> None:
    root, config = release
    path = root / "preview.json"
    document = json.loads(path.read_text())
    previous = next(item for item in document["files"] if item.endswith(".whl"))
    document["files"][name] = document["files"].pop(previous)
    write_json(path, document)
    with pytest.raises(ValueError, match="Invalid asset filename"):
        assets.assemble(root, SHA, config)


def test_wheel_metadata_mismatch_fails_even_with_updated_hash(
    release: tuple[Path, Path],
) -> None:
    root, config = release
    wheel = next(root.glob("*.whl"))
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("wrong.dist-info/METADATA", "Name: wrong\nVersion: 9\n")
    path = root / "preview.json"
    document = json.loads(path.read_text())
    document["files"][wheel.name] = assets._hash(wheel)
    write_json(path, document)
    with pytest.raises(ValueError, match="Wheel package/version mismatch"):
        assets.assemble(root, SHA, config)


def test_native_wrong_target_fails(release: tuple[Path, Path]) -> None:
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_text())
    document["target"]["platform"] = "linux"
    write_json(path, document)
    with pytest.raises(ValueError, match="Native release identity mismatch"):
        assets.assemble(root, SHA, config)


def test_release_manifest_tampering_fails(release: tuple[Path, Path]) -> None:
    root, config = release
    path = assets.assemble(root, SHA, config)
    document = json.loads(path.read_text())
    document["files"].pop("Scopecat-windows.exe")
    write_json(path, document)
    with pytest.raises(ValueError, match="manifest differs"):
        assets.verify(root, SHA, config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_version", "0.4.0"),
        ("build_number", 10),
        ("sources", {"public": "b" * 40}),
    ],
)
def test_native_wrong_identity_fails(
    release: tuple[Path, Path], field: str, value: object
) -> None:
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_text())
    document[field] = value
    write_json(path, document)
    with pytest.raises(ValueError, match="Native release identity mismatch"):
        assets.assemble(root, SHA, config)


def test_duplicate_keys_and_symlinks_fail(release: tuple[Path, Path]) -> None:
    root, config = release
    path = root / "preview.json"
    original = path.read_text()
    path.write_text(original.replace('"format": 1', '"format": 1, "format": 1'))
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        assets.assemble(root, SHA, config)
    path.write_text(original)
    ui = root / "scopecat-ui.zip"
    ui.unlink()
    ui.symlink_to(config)
    with pytest.raises(ValueError, match="forbidden symlink"):
        assets.assemble(root, SHA, config)


def test_fixture_contract_matches_repository_producers() -> None:
    """Keep the offline fixture tied to actual source package and platform metadata."""
    import tomllib

    from lab_tools.release_identity import ReleaseIdentity

    repository = SCRIPT.parents[1]
    directories = (
        "packages/scopecat",
        "packages/scopecat-server",
        "packages/scopecat-instruments",
        "packages/scopecat-quantum",
        "packages/lab-tools",
        "packages/lab-teaching",
        "testing/scopecat-testkit",
    )
    names = {
        tomllib.loads((repository / directory / "pyproject.toml").read_text())[
            "project"
        ]["name"]
        for directory in directories
    }
    assert names == set(assets.PACKAGES)
    identity = ReleaseIdentity("0.3.0-rc.1", 9)
    assert identity.python_package_version("0.1.0", SHA, "1770000000") == (
        f"0.1.0.dev1770000000+scopecat.0.3.0rc1.g{SHA[:12]}"
    )
    assert identity.javascript_package_version("0.1.0", SHA) == (
        f"0.1.0-scopecat.0.3.0.rc.1.g{SHA[:12]}"
    )
    target = target_identity()
    assert target["platform"] in {"darwin", "win32", "linux"}
    assert all(isinstance(value, str) for value in target.values())


@pytest.mark.parametrize("missing", [False, True])
def test_native_public_wheels_must_match_preview(
    release: tuple[Path, Path], missing: bool
) -> None:
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_text())
    wheel = next(name for name in document["files"] if name.startswith("wheels/"))
    if missing:
        del document["files"][wheel]
    else:
        document["files"][wheel] = "0" * 64
    write_json(path, document)
    # Keep the outer manifest hash valid to isolate the payload provenance check.
    native_path = root / "macos-native-release.json"
    native = json.loads(native_path.read_text())
    native["bundle_sha256"] = assets._hash(path)
    write_json(native_path, native)
    with pytest.raises(ValueError, match="Native author wheel inventory mismatch"):
        assets.assemble(root, SHA, config)


@pytest.mark.parametrize(
    "extra",
    [
        "wheels/scopecat_quantum-1-py3-none-any.whl",
        "wheels/numpy-1-py3-none-any.whl",
        "toolchain/python.tar",
        "toolchain/uv.exe",
        "gui/../escape",
        "gui/../../outside",
    ],
)
def test_native_resources_reject_unselected_files(release, extra):
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_bytes())
    document["files"][extra] = "0" * 64
    write_json(path, document)
    native_path = root / "macos-native-release.json"
    native = json.loads(native_path.read_bytes())
    native["bundle_sha256"] = assets._hash(path)
    write_json(native_path, native)
    with pytest.raises(ValueError, match=r"Native author wheel|Invalid native author"):
        assets.assemble(root, SHA, config)


@pytest.mark.parametrize(
    "missing", ["author.json", "requirements.lock", "gui/index.html"]
)
def test_native_resources_require_recipe_and_gui(release, missing):
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_bytes())
    del document["files"][missing]
    write_json(path, document)
    with pytest.raises(ValueError, match="Incomplete native author resources"):
        assets.assemble(root, SHA, config)


def test_legacy_full_delivery_is_not_a_native_author_payload(release):
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_bytes())
    document["format"] = 1
    write_json(path, document)
    with pytest.raises(ValueError, match="Native release identity mismatch"):
        assets.assemble(root, SHA, config)


def test_native_recipe_requires_a_sha256_digest(release):
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_bytes())
    document["files"]["author.json"] = "g" * 64
    write_json(path, document)
    with pytest.raises(ValueError, match="Invalid native author resource"):
        assets.assemble(root, SHA, config)
