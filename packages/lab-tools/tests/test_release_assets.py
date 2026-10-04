"""Offline release rehearsal checks completeness and immutable provenance."""

from __future__ import annotations

import importlib.util
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from lab_tools.bundle import target_identity

SCRIPT = Path(__file__).resolve().parents[3] / "scripts/release_assets.py"
spec = importlib.util.spec_from_file_location("release_assets", SCRIPT)
assert spec is not None and spec.loader is not None
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)
SHA = "a" * 40


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
            "target": {**target_identity(), "platform": target},
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
        write_json(root / f"{platform}-bundle.json", bundle)
        write_json(
            root / f"{platform}-native-release.json",
            {
                "format": 1,
                "installer": installer.name,
                "sha256": assets._hash(installer),
                "bundle_sha256": assets._hash(root / f"{platform}-bundle.json"),
                "sources": bundle["sources"],
                "target": bundle["target"],
                "runtime": bundle["runtime"],
                "build_id": bundle["build_id"],
            },
        )
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
    wheel = next(iter(document["files"]))
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
    with pytest.raises(ValueError, match="Native public wheel inventory mismatch"):
        assets.assemble(root, SHA, config)
