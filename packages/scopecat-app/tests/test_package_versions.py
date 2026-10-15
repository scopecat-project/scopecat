"""Real wheel metadata and resolver switching for coordinated first-party batches."""
# ruff: noqa: S603, S607 -- fixed local build/install tools and test-owned paths

import json
import os
import subprocess
import sys
import tomllib
import zipfile
from email.parser import BytesParser

import pytest
from packaging.version import Version
from scripts.app_build.package_versions import pin, stamp, validate_dependencies
from scripts.app_build.release_identity import ReleaseIdentity


def test_pins_preserve_extras_markers_and_external_requirements():
    assert pin('scopecat-app[kernel]; python_version >= "3.14"', "0.1.0rc1") == (
        'scopecat-app[kernel]==0.1.0rc1; python_version >= "3.14"'
    )
    assert pin("numpy>=2", "0.1.0") == "numpy>=2"
    with pytest.raises(ValueError, match="direct URLs"):
        pin("scopecat @ https://example.invalid/core.whl", "0.1.0")
    with pytest.raises(ValueError, match="must select"):
        validate_dependencies(["scopecat>=0.1"], "0.1.0")


def test_real_wheels_and_explicit_resolver_switch(tmp_path):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    identity = ReleaseIdentity("0.1.0", 1)
    dev = identity.python_package_version("a" * 40, "123")
    assert dev == identity.python_package_version("a" * 40, "123")
    assert Version("0.1.0") < Version("0.2.0")
    versions = ("0.2.0", "0.1.0", "0.1.0rc1", dev)
    for name, dependencies in (
        ("scopecat", []),
        ("scopecat-server", ["scopecat>=0.2"]),
    ):
        project = tmp_path / name
        module = project / "src" / name.replace("-", "_")
        module.mkdir(parents=True)
        (module / "__init__.py").write_text("")
        metadata = project / "pyproject.toml"
        metadata.write_text(
            f'[project]\nname="{name}"\nversion="0.2.0"\n'
            f"dependencies={json.dumps(dependencies)}\n"
            '[build-system]\nrequires=["uv_build>=0.13,<0.14"]\n'
            'build-backend="uv_build"\n'
            '[project.optional-dependencies]\nall=["scopecat>=0.2"]\n'
            '[dependency-groups]\ndev=["scopecat>=0.2"]\n'
        )
        for version in versions:
            stamp(metadata, version)
            assert tomllib.loads(metadata.read_text())["dependency-groups"]["dev"] == [
                "scopecat>=0.2"
            ]
            subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import sys; from uv_build import build_wheel; "
                        "build_wheel(sys.argv[1])"
                    ),
                    str(wheels),
                ],
                cwd=project,
                check=True,
                capture_output=True,
            )
    for wheel in wheels.glob("*.whl"):
        with zipfile.ZipFile(wheel) as archive:
            path = next(p for p in archive.namelist() if p.endswith("/METADATA"))
            metadata = BytesParser().parsebytes(archive.read(path))
        validate_dependencies(
            metadata.get_all("Requires-Dist", []), str(metadata["Version"])
        )
    environment = tmp_path / "environment"
    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(environment)],
        check=True,
        capture_output=True,
    )
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    base = [
        "uv",
        "pip",
        "install",
        "--python",
        str(python),
        "--no-index",
        "--find-links",
        str(wheels),
    ]
    for version in versions:
        subprocess.run(
            [*base, f"scopecat-server[all]=={version}"], check=True, capture_output=True
        )
        output = subprocess.check_output(
            [
                str(python),
                "-c",
                (
                    "from importlib.metadata import version; "
                    'print(version("scopecat"), version("scopecat-server"))'
                ),
            ],
            text=True,
        )
        assert output.strip() == f"{version} {version}"
    rejected = subprocess.run(
        [*base, "scopecat-server==0.1.0", "scopecat==0.2.0"], capture_output=True
    )
    assert rejected.returncode != 0
