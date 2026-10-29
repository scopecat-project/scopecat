"""Offline release rehearsal checks completeness and immutable provenance."""

from __future__ import annotations

import importlib.util
import json
import os
import platform as python_platform
import shutil
import subprocess
import textwrap
import zipfile
from pathlib import Path
from typing import Any

import pytest
from scripts.app_build.author_resources import build as build_author_resources
from scripts.app_build.author_resources import native_author_inputs

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat_app.bundle import file_hash, inventory, target_identity, verify_bundle

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


def installer_name(platform: str) -> str:
    from scripts.app_build.release_identity import ReleaseIdentity, installer_filename

    return installer_filename(
        ReleaseIdentity("0.3.0-rc.1", 9),
        {**target_identity(), "platform": assets.NATIVE[platform][0]},
    )


@pytest.fixture
def release(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "assets"
    root.mkdir()
    config = tmp_path / "release.toml"
    config.write_text('version = "0.3.0-rc.1"\nbuild_number = 9\n')
    version = "0.3.0rc1"
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
            "ui_version": "0.3.0-rc.1",
            "packages": dict.fromkeys(assets.PACKAGES, version),
            "files": files,
        },
    )
    for platform, (target, _suffix) in assets.NATIVE.items():
        installer = root / installer_name(platform)
        installer.write_bytes(platform.encode())
        bundle = {
            "format": 1,
            "release_version": "0.3.0-rc.1",
            "public_version": "0.3.0-rc.1",
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
                    "scopecat_tutorials": "scopecat-tutorials",
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
        portable = {
            "python": python_platform.python_version(),
            "target": target_identity(),
            "distribution": {"key": "fixture", "url": "https://example.org/python.tar"},
            "distribution_build": "fixture",
            "uv": "0.13.5",
            "archive_sha256": file_hash(source / "toolchain/python.tar"),
        }
        write_json(source / "toolchain/runtime.json", portable)
        bundle["build_tools"] = {
            "python": python_platform.python_version(),
            "uv": "uv 0.13.5",
            "pnpm": "prebuilt GUI",
            "portable_python": portable,
        }
        bundle["files"] = inventory(source, ("gui", "wheels", "toolchain"))
        for name in (
            "requirements.lock",
            "dependencies.lock",
            "build.lock",
            "install.py",
        ):
            (source / name).write_text("", encoding="utf-8")
            bundle["files"][name] = file_hash(source / name)
        bundle["build_id"] = sha256_json_hash(
            {key: bundle[key] for key in ("sources", "files", "target")}
        )
        write_json(source / "bundle.json", bundle)
        resources = tmp_path / platform / "resources"
        resources.mkdir()
        payload = build_author_resources(
            native_author_inputs(source, verify_bundle(source)), resources / "payload"
        )
        projected = json.loads((payload / "bundle.json").read_bytes())
        # Platform declarations are simulated; projection and report writing are real.
        projected["target"]["platform"] = target
        projected["build_tools"]["portable_python"]["target"]["platform"] = target
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
    assert len(document["files"]) == 11
    with pytest.raises(FileExistsError, match="never overwrite"):
        assets.assemble(root, SHA, config)


@pytest.mark.parametrize("platform", ["darwin", "win32"])
@pytest.mark.parametrize(
    "public,version,channel",
    [
        ("0.2.0", "0.2.0", "release"),
        ("0.3.0-rc.1", "0.3.0-rc.1", "prerelease"),
        ("0.2.0", "0.2.0-dev", "development"),
        ("0.3.0-rc.1", "0.3.0-rc.1-dev", "development"),
    ],
)
def test_native_report_separates_channel_and_qualification(
    tmp_path: Path, platform: str, public: str, version: str, channel: str
) -> None:
    resources = tmp_path / "resources"
    (resources / "payload").mkdir(parents=True)
    bundle = {
        "public_version": public,
        "release_version": version,
        "build_number": 2,
        "sources": {"public": SHA},
        "target": {"platform": platform},
        "runtime": {},
        "build_id": "sha256:" + "b" * 64,
    }
    write_json(resources / "payload/bundle.json", bundle)
    installer = tmp_path / "installer"
    installer.write_bytes(b"synthetic installer")
    native_producer.write_release_manifest(resources, tmp_path, installer)
    report = json.loads((tmp_path / "native-release.json").read_bytes())
    assert report["format"] == 2
    assert report["channel"] == channel
    assert "distribution" not in report
    assert report["signing"] == {
        "application": "ad-hoc" if platform == "darwin" else "unsigned",
        "installer": "unsigned",
    }
    assert report["notarization"] == (
        "not-notarized" if platform == "darwin" else "not-applicable"
    )
    assert report["native_interaction"] == "not-evaluated"


@pytest.mark.parametrize(
    "field,value",
    [
        ("channel", "release"),
        ("signing", {"application": "trusted"}),
        ("notarization", "notarized"),
        ("native_interaction", "passed"),
        ("distribution", "prerelease"),
        ("format", 3),
        ("format", True),
        ("format", 1),
    ],
)
def test_release_rejects_false_qualification(
    release: tuple[Path, Path], field: str, value: object
) -> None:
    root, config = release
    path = root / "macos-native-release.json"
    report = json.loads(path.read_bytes())
    report[field] = value
    write_json(path, report)
    with pytest.raises(ValueError, match="qualification"):
        assets.assemble(root, SHA, config)


def test_legacy_qualification_remains_verifiable_without_rewriting(
    release: tuple[Path, Path], tmp_path: Path
) -> None:
    root, config = release
    original = {}
    for platform in assets.NATIVE:
        path = root / f"{platform}-native-release.json"
        report = json.loads(path.read_bytes())
        report["format"] = 1
        for key in ("channel", "signing", "notarization"):
            del report[key]
        report["distribution"] = "prerelease; no trusted signing/notarization"
        write_json(path, report)
        original[path.name] = path.read_text()
    assets.assemble(root, SHA, config)
    published = tmp_path / "published"
    path = assets.export(root, published, SHA, config)
    assert assets.verify(published, SHA, config) == path
    document = json.loads(path.read_bytes())
    assert len(list(published.iterdir())) == 12
    for name, content in original.items():
        assert document["qualification"][name] == content


@pytest.mark.parametrize(
    "name", ["scopecat-ui.zip", installer_name("macos"), "windows-bundle.json"]
)
def test_missing_assets_fail(release: tuple[Path, Path], name: str) -> None:
    root, config = release
    (root / name).unlink()
    with pytest.raises(
        ValueError, match=r"Missing asset|Incomplete native qualification"
    ):
        assets.assemble(root, SHA, config)
    assert not (root / "release.json").exists()


def test_wrong_commit_and_corrupt_hash_fail(release: tuple[Path, Path]) -> None:
    root, config = release
    with pytest.raises(ValueError, match="identity mismatch"):
        assets.assemble(root, "b" * 40, config)
    (root / installer_name("windows")).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        assets.assemble(root, SHA, config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_version", "0.4.0"),
        ("build_number", 10),
        ("channel", "preview"),
    ],
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
    "ui_version", ["0.4.0", None, f"0.1.0-scopecat.0.3.0.rc.1.g{SHA[:12]}"]
)
def test_ui_release_identity_rejected_before_asset_assembly(tmp_path: Path, ui_version):
    config = tmp_path / "release.toml"
    config.write_text('version="0.3.0-rc.1"\nbuild_number=9\n')
    write_json(
        tmp_path / "preview.json",
        {
            "format": 1,
            "commit": SHA,
            "release_version": "0.3.0-rc.1",
            "build_number": 9,
            "channel": "release",
            "ui_version": ui_version,
        },
    )
    with pytest.raises(ValueError, match="UI release identity mismatch"):
        assets.inventory(tmp_path, SHA, config)


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
    document["files"].pop(installer_name("windows"))
    write_json(path, document)
    with pytest.raises(ValueError, match="manifest differs"):
        assets.verify(root, SHA, config)


@pytest.mark.parametrize(
    "field,value",
    [
        ("release_version", "0.4.0"),
        ("build_number", 10),
        ("sources", {"public": "b" * 40}),
        ("public_version", None),
        ("build_number", "9"),
        ("build_id", None),
        ("build_id", "sha256:" + "0" * 64),
        ("runtime", {}),
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

    from scripts.app_build.release_identity import ReleaseIdentity

    repository = SCRIPT.parents[1]
    directories = (
        "packages/scopecat",
        "packages/scopecat-server",
        "packages/scopecat-instruments",
        "packages/scopecat-quantum",
        "packages/scopecat-app",
        "packages/scopecat-tutorials",
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
    assert identity.python_package_version(SHA, "1770000000", release=True) == (
        "0.3.0rc1"
    )
    assert identity.javascript_package_version() == "0.3.0-rc.1"
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
def test_native_resources_reject_unselected_files(release: tuple[Path, Path], extra):
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
def test_native_resources_require_recipe_and_gui(release: tuple[Path, Path], missing):
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_bytes())
    del document["files"][missing]
    write_json(path, document)
    with pytest.raises(ValueError, match="Incomplete native author resources"):
        assets.assemble(root, SHA, config)


def test_legacy_full_delivery_is_not_a_native_author_payload(
    release: tuple[Path, Path],
):
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_bytes())
    document["format"] = 1
    write_json(path, document)
    with pytest.raises(ValueError, match="Native release identity mismatch"):
        assets.assemble(root, SHA, config)


def test_native_recipe_requires_a_sha256_digest(release: tuple[Path, Path]):
    root, config = release
    path = root / "macos-bundle.json"
    document = json.loads(path.read_bytes())
    document["files"]["author.json"] = "g" * 64
    write_json(path, document)
    with pytest.raises(ValueError, match="Invalid native author resource"):
        assets.assemble(root, SHA, config)


def test_export_uses_real_projection_and_keeps_preview_consumers(
    release: tuple[Path, Path], tmp_path: Path
):
    from scripts.app_build.preview import read_preview

    root, config = release
    assets.assemble(root, SHA, config)
    destination = tmp_path / "published"
    manifest = assets.export(root, destination, SHA, config)
    assert assets.verify(destination, SHA, config) == manifest
    document = json.loads(manifest.read_bytes())
    assert len(list(destination.iterdir())) == 12
    assert set(document["files"]) == {p.name for p in destination.iterdir()} - {
        "release.json"
    }
    assert len(document["qualification"]) == 4
    assert not list(destination.glob("*-bundle.json"))
    assert not list(destination.glob("*-native-release.json"))
    preview = read_preview(destination / document["preview"]["file"])
    assert assets._hash(destination / "preview.json") == document["preview"]["sha256"]
    assert set(preview["packages"]) == assets.PACKAGES
    for name, digest in preview["files"].items():
        assert assets._hash(destination / name) == digest == document["files"][name]
    for installer in document["installers"].values():
        assert assets._hash(destination / installer["file"]) == installer["sha256"]
    with pytest.raises(FileExistsError):
        assets.export(root, destination, SHA, config)


@pytest.mark.parametrize(
    "change", ["evidence", "installer", "extra", "missing", "inventory"]
)
def test_export_rejects_tampering(release: tuple[Path, Path], tmp_path: Path, change):
    root, config = release
    assets.assemble(root, SHA, config)
    destination = tmp_path / "published"
    manifest = assets.export(root, destination, SHA, config)
    document = json.loads(manifest.read_bytes())
    if change == "evidence":
        document["qualification"]["macos-bundle.json"] += " "
    elif change == "installer":
        name = document["installers"]["macos"]["file"]
        (destination / name).write_bytes(b"corrupt")
    elif change == "extra":
        (destination / "macos-bundle.json").write_text("{}")
    elif change == "missing":
        del document["qualification"]["macos-bundle.json"]
    else:
        document["installers"]["macos"]["file"] = "other.dmg"
    write_json(manifest, document)
    with pytest.raises(
        ValueError, match=r"checksum mismatch|qualification evidence|manifest differs"
    ):
        assets.verify(destination, SHA, config)


def test_export_preserves_windows_evidence_bytes(
    release: tuple[Path, Path], tmp_path: Path
):
    root, config = release
    bundle = root / "windows-bundle.json"
    bundle.write_bytes(
        json.dumps(json.loads(bundle.read_bytes()), indent=2)
        .replace("\n", "\r\n")
        .encode()
    )
    native = root / "windows-native-release.json"
    document = json.loads(native.read_bytes())
    document["bundle_sha256"] = assets._hash(bundle)
    write_json(native, document)
    assets.assemble(root, SHA, config)
    destination = tmp_path / "published"
    assets.export(root, destination, SHA, config)
    assets.verify(destination, SHA, config)


@pytest.mark.parametrize("dependency", ["scopecat>=0.1", "scopecat==0.2.0"])
def test_release_rejects_mixed_batch_dependencies(
    release: tuple[Path, Path], dependency
):
    root, config = release
    wheel = next(root.glob("scopecat_server-*.whl"))
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "scopecat_server-0.3.0rc1.dist-info/METADATA",
            f"Name: scopecat-server\nVersion: 0.3.0rc1\nRequires-Dist: {dependency}\n",
        )
    path = root / "preview.json"
    document = json.loads(path.read_text())
    document["files"][wheel.name] = assets._hash(wheel)
    write_json(path, document)
    with pytest.raises(ValueError, match="First-party dependency must select"):
        assets.assemble(root, SHA, config)


@pytest.mark.parametrize("damage", ["missing", "invalid", "wrong-target"])
def test_release_assembly_requires_portable_provenance_even_with_matching_report(
    release: tuple[Path, Path], damage: str
) -> None:
    root, config = release
    manifest = root / "windows-bundle.json"
    document = json.loads(manifest.read_text())
    if damage == "missing":
        del document["build_tools"]
    elif damage == "invalid":
        document["build_tools"]["portable_python"] = []
    else:
        document["build_tools"]["portable_python"]["target"]["platform"] = "linux"
    write_json(manifest, document)
    report = root / "windows-native-release.json"
    native = json.loads(report.read_text())
    native["bundle_sha256"] = file_hash(manifest)
    write_json(report, native)
    with pytest.raises(ValueError, match="provenance"):
        assets.assemble(root, SHA, config)
    assert not (root / "release.json").exists()
