"""Assemble server distributions from Python sources and a built UI."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from collections.abc import Iterable
from email.parser import Parser
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SERVER_ROOT = REPOSITORY_ROOT / "packages" / "scopecat-server"
UI_DIST = REPOSITORY_ROOT / "apps" / "scopecat-ui" / "dist"
DIST_ROOT = REPOSITORY_ROOT / "dist" / "scopecat-server"
PACKAGES = ("scopecat", "scopecat-server", "scopecat-instruments", "scopecat-quantum")
STATIC_INDEX = "scopecat_server/static/index.html"
ASSET_RE = re.compile(r'(?:src|href)="(/assets/[^"]+)"')


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="scopecat-pilot-artifacts-") as temporary:
        output = Path(temporary) / "bundle"
        _build(output)
        shutil.rmtree(DIST_ROOT, ignore_errors=True)
        shutil.copytree(output, DIST_ROOT)
    print(f"installable pilot bundle: {DIST_ROOT}")


def _build(output: Path) -> None:
    if not (UI_DIST / "index.html").is_file():
        raise RuntimeError("UI bundle is missing; run `pnpm run build` first")
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is required to build server distributions")

    git = shutil.which("git")
    if git is None:
        raise RuntimeError("git is required to identify the bundle source")
    build_info = {
        "source_commit": subprocess.check_output(  # noqa: S603 - resolved executable, fixed arguments
            [git, "rev-parse", "HEAD"],
            cwd=REPOSITORY_ROOT,
            text=True,
        ).strip(),
        "source_dirty": bool(
            subprocess.check_output(  # noqa: S603 - fixed git query
                [git, "status", "--porcelain", "--untracked-files=normal"],
                cwd=REPOSITORY_ROOT,
                text=True,
            ).strip()
        ),
        "python_requirement": tomllib.loads(
            (REPOSITORY_ROOT / "pyproject.toml").read_text()
        )["project"]["requires-python"],
        "ui_version": json.loads(
            (REPOSITORY_ROOT / "apps/scopecat-ui/package.json").read_text()
        )["version"],
        "lock_sha256": {
            str(path.relative_to(REPOSITORY_ROOT)): _sha256(path)
            for path in (
                REPOSITORY_ROOT / "uv.lock",
                REPOSITORY_ROOT / "apps/scopecat-ui/pnpm-lock.yaml",
            )
        },
    }
    with tempfile.TemporaryDirectory(prefix="scopecat-server-build-") as temporary:
        staged_server = Path(temporary) / "scopecat-server"
        shutil.copytree(SERVER_ROOT, staged_server)
        staged_static = staged_server / "src" / "scopecat_server" / "static"
        shutil.rmtree(staged_static, ignore_errors=True)
        shutil.copytree(UI_DIST, staged_static)
        (staged_static / "build-info.json").write_text(
            json.dumps(build_info, indent=2) + "\n",
            encoding="utf-8",
        )
        subprocess.run(  # noqa: S603 - resolved executable and fixed arguments
            [
                uv,
                "build",
                str(staged_server),
                "--out-dir",
                str(output),
                "--clear",
                "--no-sources",
            ],
            check=True,
        )

    _verify_distributions(output)
    for package in PACKAGES:
        if package != "scopecat-server":
            subprocess.run(  # noqa: S603 - resolved executable, fixed packages
                [
                    uv,
                    "build",
                    "--package",
                    package,
                    "--wheel",
                    "--no-sources",
                    "--out-dir",
                    str(output),
                ],
                cwd=REPOSITORY_ROOT,
                check=True,
            )
    requirements = subprocess.check_output(  # noqa: S603 - fixed export arguments
        [
            uv,
            "export",
            "--locked",
            "--no-dev",
            "--no-emit-workspace",
            "--no-annotate",
            "--no-header",
            *[arg for package in PACKAGES for arg in ("--package", package)],
        ],
        cwd=REPOSITORY_ROOT,
        text=True,
    )
    wheels = sorted(output.glob("*.whl"))
    requirements += (
        "\n"
        + "\n".join(
            f"./{wheel.name} --hash=sha256:{_sha256(wheel)}" for wheel in wheels
        )
        + "\n"
    )
    (output / "requirements.txt").write_text(requirements, encoding="utf-8")
    packages: dict[str, str] = {}
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            metadata = Parser().parsestr(
                archive.read(
                    _only_name_ending(set(archive.namelist()), ".dist-info/METADATA")
                ).decode()
            )
            packages[str(metadata["Name"])] = str(metadata["Version"])
    manifest = {
        **build_info,
        "packages": packages,
        "files": {
            path.name: _sha256(path)
            for path in sorted(output.iterdir())
            if path.is_file()
        },
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _verify_distributions(output: Path) -> None:
    wheel = _only(output.glob("scopecat_server-*.whl"), "server wheel")
    source = _only(output.glob("scopecat_server-*.tar.gz"), "server sdist")

    with zipfile.ZipFile(wheel) as archive:
        _verify_bundle(
            names=set(archive.namelist()),
            index=archive.read(STATIC_INDEX).decode(),
            prefix="scopecat_server/static/",
        )
    with tarfile.open(source, "r:gz") as archive:
        names = set(archive.getnames())
        index_name = _only_name_ending(names, f"/src/{STATIC_INDEX}")
        extracted = archive.extractfile(index_name)
        if extracted is None:
            raise RuntimeError(f"cannot read {index_name} from {source.name}")
        _verify_bundle(
            names=names,
            index=extracted.read().decode(),
            prefix=index_name.removesuffix("index.html"),
        )

    print(f"verified GUI bundle in {wheel.name} and {source.name}")


def _verify_bundle(*, names: set[str], index: str, prefix: str) -> None:
    assets = {match.group(1).removeprefix("/") for match in ASSET_RE.finditer(index)}
    if not assets:
        raise RuntimeError("GUI index does not reference any built assets")
    missing = sorted(
        f"{prefix}{asset}" for asset in assets if f"{prefix}{asset}" not in names
    )
    if missing:
        raise RuntimeError(f"GUI bundle is missing referenced assets: {missing}")


def _only(paths: Iterable[Path], label: str) -> Path:
    selected = tuple(paths)
    if len(selected) != 1:
        raise RuntimeError(f"expected exactly one {label}, found {len(selected)}")
    return selected[0]


def _only_name_ending(names: set[str], suffix: str) -> str:
    selected = tuple(name for name in names if name.endswith(suffix))
    if len(selected) != 1:
        raise RuntimeError(
            f"expected exactly one archive member ending in {suffix}, "
            f"found {len(selected)}"
        )
    return selected[0]


if __name__ == "__main__":
    main()
