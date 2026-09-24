"""Build a locked platform-specific offline application or laboratory delivery."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tomllib
import zipfile
from dataclasses import dataclass
from email.parser import BytesParser
from pathlib import Path, PureWindowsPath
from typing import Protocol, cast
from uuid import uuid4

from filelock import FileLock, Timeout
from packaging.utils import canonicalize_name

from lab_tools.bundle import (
    CURRENT_DELIVERY,
    MANIFEST,
    file_hash,
    inventory,
    managed_path,
    target_identity,
    verify_bundle,
)
from scopecat.kernel.content_identity import sha256_content_hash, sha256_json_hash

REPOSITORY = Path.cwd()
MODULES = {
    "scopecat": "scopecat",
    "scopecat_server": "scopecat-server",
    "lab_teaching": "scopecat-lab-teaching",
}


class BuildArguments(Protocol):
    destination: Path | None
    output_home: Path | None
    release: bool
    notebook: bool
    source: Path | None
    gui: Path | None
    recipe: Path | None


@dataclass(frozen=True, slots=True)
class DeliveryRecipe:
    lock_project: Path
    public_source: Path
    dependency_group: str
    include_project: bool
    packages: tuple[Path, ...]


def load_recipe(path: Path, *, public_source: Path | None = None) -> DeliveryRecipe:
    """Resolve a maintained recipe without evaluating Python or shell commands."""
    path = path.resolve()
    document = cast(
        "dict[str, object]", tomllib.loads(path.read_text(encoding="utf-8"))
    )
    if set(document) != {"delivery"} or not isinstance(document["delivery"], dict):
        raise ValueError("recipe requires only a [delivery] table")
    table = cast("dict[str, object]", document["delivery"])
    required = {
        "lock_project",
        "dependency_group",
        "include_project",
        "packages",
    }
    if not required <= set(table) or set(table) - required - {"public_source"}:
        raise ValueError(
            "delivery recipe requires lock_project, dependency_group, "
            "include_project and packages"
        )

    def directory(value: object, root: Path) -> Path:
        if (
            not isinstance(value, str)
            or not value.strip()
            or Path(value).is_absolute()
            or PureWindowsPath(value).drive
            or "\\" in value
            or ".." in Path(value).parts
        ):
            raise ValueError(
                "recipe paths must be relative directories within their selected root"
            )
        selected = (root / value).resolve()
        if (
            not selected.is_relative_to(root)
            or not (selected / "pyproject.toml").is_file()
        ):
            raise ValueError(
                f"recipe package/project lacks a local pyproject.toml: {value}"
            )
        return selected

    if public_source is not None:
        public = public_source.resolve()
        if not (public / "pyproject.toml").is_file():
            raise ValueError(f"selected public source lacks pyproject.toml: {public}")
    elif "public_source" in table:
        public = directory(table["public_source"], path.parent)
    else:
        raise ValueError(
            "recipe requires public_source or an explicit --source checkout"
        )

    def package(value: object) -> Path:
        if isinstance(value, dict):
            reference = cast("dict[str, object]", value)
            if set(reference) != {"source", "path"} or reference["source"] != "public":
                raise ValueError("package reference requires source='public' and path")
            return directory(reference["path"], public)
        return directory(value, path.parent)

    group = table["dependency_group"]
    include = table["include_project"]
    packages = table["packages"]
    if not isinstance(group, str) or not group.strip() or group.startswith("-"):
        raise ValueError("dependency_group must name a locked dependency group")
    if not isinstance(include, bool):
        raise ValueError("include_project must be a boolean")
    if not isinstance(packages, list) or not packages:
        raise ValueError("packages must list the local packages to build")
    result = DeliveryRecipe(
        directory(table["lock_project"], path.parent),
        public,
        group,
        include,
        tuple(package(item) for item in cast("list[object]", packages)),
    )
    if len(set(result.packages)) != len(result.packages):
        raise ValueError("recipe packages contains duplicate directories")
    if (
        not (result.lock_project / "uv.lock").is_file()
        or not (result.public_source / "uv.lock").is_file()
    ):
        raise ValueError("recipe projects require retained uv.lock files")
    return result


def _default_recipe(repository: Path, notebook: bool) -> DeliveryRecipe:
    return DeliveryRecipe(
        repository,
        repository,
        "delivery-notebook" if notebook else "delivery",
        False,
        tuple(
            repository / "packages" / name
            for name in (
                "scopecat",
                "scopecat-server",
                "scopecat-instruments",
                "lab-teaching",
                "lab-tools",
            )
        ),
    )


def _package_names(packages: tuple[Path, ...]) -> set[str]:
    names: set[str] = set()
    for package in packages:
        metadata = cast(
            "dict[str, object]",
            tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8")),
        )
        project = metadata.get("project")
        name = (
            cast("dict[str, object]", project).get("name")
            if isinstance(project, dict)
            else None
        )
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"local package must declare project.name: {package}")
        normalized = canonicalize_name(name)
        if normalized in names:
            raise ValueError(f"recipe builds duplicate distribution {normalized}")
        names.add(normalized)
    return names


def _unique_wheels(wheels: Path) -> dict[str, tuple[Path, str]]:
    selected: dict[str, tuple[Path, str]] = {}
    for path in sorted(wheels.glob("*.whl")):
        name, version = wheel_metadata(path)
        # Distribution names normalize runs of -, _ and . to a hyphen.
        normalized = canonicalize_name(name)
        if normalized in selected:
            raise ValueError(f"multiple wheels for distribution {normalized}")
        selected[normalized] = (path, version)
    return selected


def run(command: list[str], *, cwd: Path) -> None:
    print("执行: " + " ".join(command), flush=True)
    _ = subprocess.run(command, cwd=cwd, check=True)  # noqa: S603 - explicit local tool and argument list


def wheel_metadata(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata = next(
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        )
        message = BytesParser().parsebytes(archive.read(metadata))
        return str(message["Name"]), str(message["Version"])


def build_delivery(
    destination: Path,
    *,
    release: bool = False,
    notebook: bool = False,
    source: Path | None = None,
    gui: Path | None = None,
    recipe: Path | None = None,
) -> Path:
    if recipe is not None and notebook:
        raise ValueError("recipe cannot be combined with notebook override")
    plan = (
        load_recipe(recipe, public_source=source)
        if recipe is not None
        else _default_recipe((source or REPOSITORY).resolve(), notebook)
    )
    public = plan.public_source
    repository = plan.lock_project
    sources_repositories = (
        (("public", public),)
        if public == repository
        else (("public", public), ("lab", repository))
    )
    destination = destination.resolve()
    local_names = _package_names(plan.packages)
    if release:
        for _, repo in sources_repositories:
            if subprocess.check_output(
                ["git", "status", "--porcelain"],  # noqa: S607 - standard maintainer tool
                cwd=repo,
                text=True,
            ).strip():
                raise ValueError("正式发布要求 public 和实验室锁定仓库工作目录均干净")
    destination.mkdir(parents=True, exist_ok=False)
    ui = public / "apps/scopecat-ui"
    pnpm = shutil.which("pnpm")
    if pnpm is None and gui is None:
        raise ValueError("维护者构建需要 pnpm")
    if gui is None:
        assert pnpm is not None
        run([pnpm, "install", "--frozen-lockfile"], cwd=ui)
        run([pnpm, "run", "build"], cwd=ui)
    _ = shutil.copytree(gui or ui / "dist", destination / "gui")
    wheels = destination / "wheels"
    wheels.mkdir()
    # Export the reviewed repository lock, excluding locally built distributions.
    # Download exact third-party artifacts without another dependency resolution.
    dependency_lock = destination / "dependencies.lock"
    run(
        [
            "uv",
            "export",
            "--locked",
            *(
                ["--no-default-groups", "--group"]
                if plan.include_project
                else ["--only-group"]
            ),
            plan.dependency_group,
            "--no-emit-local",
            "--format",
            "requirements-txt",
            "--output-file",
            str(dependency_lock),
        ],
        cwd=repository,
    )
    for package in plan.packages:
        run(
            [
                "uv",
                "build",
                "--wheel",
                "--build-constraints",
                dependency_lock.as_uri(),
                "--out-dir",
                str(wheels),
                str(package),
            ],
            cwd=repository,
        )
    run(
        [
            "uv",
            "run",
            "--isolated",
            "--locked",
            "--only-group",
            "delivery-build",
            "python",
            "-m",
            "pip",
            "download",
            "--no-deps",
            "--require-hashes",
            "--only-binary=:all:",
            "--dest",
            str(wheels),
            "-r",
            str(dependency_lock),
        ],
        cwd=public,
    )
    selected_wheels = _unique_wheels(wheels)
    if missing := local_names - selected_wheels.keys():
        raise ValueError(f"local package wheels missing: {sorted(missing)}")
    if "scopecat-lab-tools" not in selected_wheels:
        raise ValueError("交付缺少 scopecat-lab-tools wheel")
    release_version = selected_wheels["scopecat-lab-tools"][1]
    _ = shutil.copyfile(repository / "uv.lock", destination / "build.lock")
    runtime: dict[str, object] = {}
    requirements: list[str] = []
    for wheel in sorted(wheels.glob("*.whl")):
        name, version = wheel_metadata(wheel)
        requirements.append(f"{name}=={version} --hash=sha256:{file_hash(wheel)}")
        for module, package_name in MODULES.items():
            if name.replace("_", "-").lower() != package_name:
                continue
            with zipfile.ZipFile(wheel) as archive:
                files = {
                    item: sha256_content_hash(archive.read(item))
                    for item in archive.namelist()
                    if item.startswith(module + "/")
                    and not item.endswith("/")
                    and not item.endswith((".pyc", ".pyo"))
                }
            runtime[module] = {
                "distribution": package_name,
                "version": version,
                "content_hash": sha256_json_hash(files),
            }
    if set(runtime) != set(MODULES):
        raise ValueError("交付缺少科学软件 wheel")
    _ = (destination / "requirements.lock").write_text(
        "\n".join(sorted(requirements)) + "\n", encoding="utf-8"
    )
    # Copy the maintained stdlib-only installer; no second installer implementation.
    _ = shutil.copyfile(
        public / "packages/lab-tools/src/lab_tools/bundle.py",
        destination / "install.py",
    )
    files = inventory(destination, ("gui", "wheels"))
    files.update(
        {
            name: file_hash(destination / name)
            for name in (
                "requirements.lock",
                "dependencies.lock",
                "build.lock",
                "install.py",
            )
        }
    )
    sources = {
        name: subprocess.check_output(
            ["git", "rev-parse", "HEAD"],  # noqa: S607 - standard maintainer tool
            cwd=repo,
            text=True,
        ).strip()
        for name, repo in sources_repositories
    }
    for name, repo in sources_repositories:
        if subprocess.check_output(
            ["git", "status", "--porcelain"],  # noqa: S607 - standard maintainer tool
            cwd=repo,
            text=True,
        ).strip():
            sources[name] += "+dirty"
    _ = (destination / MANIFEST).write_text(
        json.dumps(
            {
                "format": 1,
                "release_version": release_version,
                "release_kind": "release" if release else "development",
                "build_id": sha256_json_hash(
                    {"sources": sources, "files": files, "target": target_identity()}
                ),
                "build_tools": {
                    "python": sys.version,
                    "uv": subprocess.check_output(
                        ["uv", "--version"],  # noqa: S607 - standard maintainer tool
                        text=True,
                    ).strip(),
                    "pnpm": "prebuilt GUI"
                    if pnpm is None
                    else subprocess.check_output(  # noqa: S603 - fixed tool commands
                        [pnpm, "--version"],
                        text=True,
                    ).strip(),
                },
                "target": target_identity(),
                "sources": sources,
                "recipe_sha256": file_hash(recipe.resolve())
                if recipe is not None
                else None,
                "runtime": runtime,
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"已构建 {len(requirements)} 个 wheels; 安装验证通过前不要交付", flush=True)
    return destination


def build_managed_delivery(
    home: Path,
    *,
    release: bool = False,
    notebook: bool = False,
    source: Path | None = None,
    gui: Path | None = None,
    recipe: Path | None = None,
) -> Path:
    """Retain every attempt, publishing only a verified build to a stable entry."""
    home = home.resolve()
    home.mkdir(parents=True, exist_ok=True)
    lock = managed_path(home, home / ".build.lock")
    try:
        with FileLock(lock, timeout=0):
            identity = uuid4().hex
            artifact = managed_path(home, home / "builds" / identity)
            print(f"本次构建目录（失败也保留）: {artifact}", flush=True)
            result = build_delivery(
                artifact,
                release=release,
                notebook=notebook,
                source=source,
                gui=gui,
                recipe=recipe,
            )
            verify_bundle(result)
            pointer = managed_path(home, home / CURRENT_DELIVERY)
            staged = managed_path(home, home / f"{CURRENT_DELIVERY}.tmp")
            staged.write_text(
                json.dumps(
                    {
                        "format": 1,
                        "build": identity,
                        "manifest_sha256": file_hash(result / MANIFEST),
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
            staged.replace(pointer)
            print(f"当前交付已选择；安装/更新时可继续使用: {home}", flush=True)
            return result
    except Timeout as error:
        raise ValueError("此交付目录已有构建正在进行；请等待完成后重试") from error


def main() -> None:
    from .bundle import configure_console

    configure_console()
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("destination", type=Path, nargs="?")
    _ = parser.add_argument("--output-home", type=Path)
    _ = parser.add_argument("--release", action="store_true")
    _ = parser.add_argument("--notebook", action="store_true")
    _ = parser.add_argument(
        "--source",
        type=Path,
        help="public checkout (also overrides recipe public_source)",
    )
    _ = parser.add_argument("--recipe", type=Path)
    _ = parser.add_argument("--gui", type=Path)
    args = cast("BuildArguments", cast("object", parser.parse_args()))
    if (args.destination is None) == (args.output_home is None):
        parser.error("请选择 destination 或 --output-home，不能同时使用")
    build = build_managed_delivery if args.output_home is not None else build_delivery
    target = args.output_home if args.output_home is not None else args.destination
    assert target is not None
    try:
        print(
            build(
                target,
                release=args.release,
                notebook=args.notebook,
                source=args.source,
                gui=args.gui,
                recipe=args.recipe,
            )
        )
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(2, f"{error}\n")


if __name__ == "__main__":
    main()
