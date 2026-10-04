"""Prepare a reviewable Knope release locally; never push, tag or publish."""

# ruff: noqa: S603, S607 -- fixed maintainer commands and explicit arguments
from __future__ import annotations

import argparse
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Protocol, cast

from lab_tools.release_identity import ReleaseIdentity, read_release

KNOPE_VERSION = "0.23.0"


def version_key(version: str) -> tuple[int, int, int, int, int]:
    """Order the supported public SemVer subset, including prereleases."""
    identity = ReleaseIdentity(version, 1)
    main, _, suffix = identity.version.partition("-")
    major, minor, patch = (int(part) for part in main.split("."))
    label, _, number = suffix.partition(".")
    return (
        major,
        minor,
        patch,
        {"alpha": 0, "beta": 1, "rc": 2, "": 3}[label],
        int(number or 0),
    )


def check_candidate(repository: Path, version: str, build_number: int) -> None:
    candidate = ReleaseIdentity(version, build_number)
    candidate.require_release()
    previous = read_release(repository / "release.toml")
    if version_key(version) <= version_key(previous.version):
        raise ValueError(
            "Release version must increase; an existing version cannot be rebuilt"
        )
    if build_number <= previous.build_number:
        raise ValueError(
            "Native build_number must increase for every release, including prereleases"
        )
    tags = subprocess.check_output(
        ["git", "tag", "--list", "v*"], cwd=repository, text=True
    )
    for tag in tags.splitlines():
        try:
            existing = version_key(tag[1:])
        except ValueError:
            continue  # Historical/non-version tags are not public release identities.
        if version_key(version) <= existing:
            raise ValueError(f"Release version must exceed existing tag {tag}")


def check_publish(repository: Path) -> None:
    """Require a newer identity than every retained numbered release tag."""
    candidate = read_release(repository / "release.toml", require_release=True)
    tags = subprocess.check_output(
        ["git", "tag", "--list", "v*"], cwd=repository, text=True
    )
    for tag in tags.splitlines():
        try:
            existing = version_key(tag[1:])
        except ValueError:
            continue
        if version_key(candidate.version) <= existing:
            raise ValueError(f"Release version must exceed existing tag {tag}")
        content = subprocess.check_output(
            ["git", "show", f"{tag}:release.toml"], cwd=repository, text=True
        )
        previous = cast("dict[str, object]", tomllib.loads(content))
        build = previous.get("build_number")
        if type(build) is not int or candidate.build_number <= build:
            raise ValueError(f"Native build_number must exceed existing tag {tag}")


def prepare(repository: Path, version: str, build_number: int) -> None:
    if subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=repository, text=True
    ).strip():
        raise ValueError("Prepare in a clean checkout; commit change files first")
    check_candidate(repository, version, build_number)
    executable = os.environ.get("KNOPE", "knope")
    installed = subprocess.check_output([executable, "--version"], text=True).strip()
    if installed != f"knope {KNOPE_VERSION}":
        raise ValueError(f"Expected knope {KNOPE_VERSION}, got {installed}")
    subprocess.run(
        [executable, "prepare-release", "--override-version", version],
        cwd=repository,
        check=True,
    )
    path = repository / "release.toml"
    path.write_text(
        re.sub(
            r"(?m)^build_number = [0-9]+$",
            f"build_number = {build_number}",
            path.read_text(),
        )
    )
    if read_release(path, require_release=True) != ReleaseIdentity(
        version, build_number
    ):
        raise ValueError(
            "Prepared release identity does not match the requested identity"
        )
    subprocess.run(["git", "add", "release.toml"], cwd=repository, check=True)


def release_notes(repository: Path, version: str) -> str:
    text = (repository / "CHANGELOG.md").read_text()
    match = re.search(rf"(?m)^## {re.escape(version)}(?: \([^\n]*\))?\n", text)
    if match is None:
        raise ValueError("Prepare the release changelog before publishing")
    following = text[match.end() :]
    end = re.search(r"(?m)^## ", following)
    return following[: end.start() if end else len(following)].strip() + "\n"


class Arguments(Protocol):
    command: str
    version: str
    build_number: int
    github_output: Path | None
    notes_file: Path | None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("prepare")
    command.add_argument("version")
    command.add_argument("--build-number", type=int, required=True)
    commands.add_parser("check-publish")
    metadata = commands.add_parser("metadata")
    metadata.add_argument("--github-output", type=Path)
    metadata.add_argument("--notes-file", type=Path)
    args = cast("Arguments", cast("object", parser.parse_args()))
    repository = Path(__file__).resolve().parents[1]
    if args.command == "prepare":
        prepare(repository, args.version, args.build_number)
    elif args.command == "check-publish":
        check_publish(repository)
    else:
        identity = read_release(repository / "release.toml", require_release=True)
        notes = release_notes(repository, identity.version)
        output = (
            f"version={identity.version}\ntag=v{identity.version}\n"
            f"prerelease={str(identity.prerelease).lower()}\n"
            f"build_number={identity.build_number}\n"
        )
        if args.github_output:
            with args.github_output.open("a") as stream:
                stream.write(output)
        else:
            print(output, end="")
        if args.notes_file:
            args.notes_file.write_text(notes)


if __name__ == "__main__":
    main()
