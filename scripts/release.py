"""Project version/build policy around Knope; publishing belongs to Knope."""

# ruff: noqa: S603, S607 -- fixed maintainer commands and explicit arguments
from __future__ import annotations

import argparse
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Protocol, cast

from packaging.version import Version

from lab_tools.release_identity import ReleaseIdentity, read_release


def version_key(version: str) -> Version:
    return Version(ReleaseIdentity(version, 1).pep440)


def check_candidate(repository: Path, version: str, build_number: int) -> None:
    candidate = ReleaseIdentity(version, build_number)
    candidate.require_release()
    previous = read_release(repository / "release.toml")
    if version_key(version) <= version_key(previous.version):
        raise ValueError("Release version must increase")
    if build_number <= previous.build_number:
        raise ValueError("Native build_number must increase")
    check_tags(repository, candidate)


def check_tags(repository: Path, candidate: ReleaseIdentity) -> None:
    tags = subprocess.check_output(
        ["git", "tag", "--list", "v[0-9]*"], cwd=repository, text=True
    )
    for tag in tags.splitlines():
        try:
            existing = version_key(tag[1:])
        except ValueError:
            continue  # SHA previews and future package tags are separate identities.
        if version_key(candidate.version) <= existing:
            raise ValueError(f"Release version must exceed existing tag {tag}")
        content = subprocess.check_output(
            ["git", "show", f"{tag}:release.toml"], cwd=repository, text=True
        )
        previous = tomllib.loads(content)
        if candidate.build_number <= previous["build_number"]:
            raise ValueError(f"Native build_number must exceed existing tag {tag}")


def check_publish(repository: Path, *, remote: bool = False) -> None:
    candidate = read_release(repository / "release.toml", require_release=True)
    check_tags(repository, candidate)
    if remote:
        # Authenticated listing includes drafts. Any API failure aborts publication.
        tags = subprocess.check_output(
            [
                "gh",
                "api",
                "--paginate",
                f"repos/{os.environ['GITHUB_REPOSITORY']}/releases",
                "--jq",
                ".[].tag_name",
            ],
            cwd=repository,
            text=True,
        )
        if f"v{candidate.version}" in tags.splitlines():
            raise ValueError("Refusing existing published or draft release")


def prepare(repository: Path, version: str | None, build_number: int) -> None:
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repository, text=True
    ).strip():
        raise ValueError("Prepare in a clean checkout; commit change files first")
    previous = read_release(repository / "release.toml")
    ReleaseIdentity(previous.version, build_number)
    if build_number <= previous.build_number:
        raise ValueError("Native build_number must increase")
    if version:
        check_candidate(repository, version, build_number)
    command = [os.environ.get("KNOPE", "knope"), "prepare-release"]
    if version:
        command.extend(("--override-version", version))
    subprocess.run(command, cwd=repository, check=True)
    path = repository / "release.toml"
    candidate = read_release(path)
    if version_key(candidate.version) <= version_key(previous.version):
        raise ValueError("Knope must prepare a newer version from change files")
    path.write_text(
        re.sub(
            r"(?m)^build_number = [0-9]+$",
            f"build_number = {build_number}",
            path.read_text(),
        )
    )
    check_publish(repository)
    subprocess.run(["git", "add", "release.toml"], cwd=repository, check=True)


class Arguments(Protocol):
    command: str
    version: str | None
    build_number: int
    remote: bool


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare")
    command.add_argument(
        "version",
        nargs="?",
        help="Optional override of Knope's changefile-derived version",
    )
    command.add_argument("--build-number", type=int, required=True)
    commands.add_parser("check-publish").add_argument("--remote", action="store_true")
    commands.add_parser("metadata")
    args = cast("Arguments", cast("object", parser.parse_args()))
    repository = Path(__file__).resolve().parents[1]
    if args.command == "prepare":
        prepare(repository, args.version, args.build_number)
    elif args.command == "check-publish":
        check_publish(repository, remote=args.remote)
    else:
        identity = read_release(repository / "release.toml", require_release=True)
        print(f"version={identity.version}\nbuild_number={identity.build_number}")


if __name__ == "__main__":
    main()
