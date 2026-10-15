"""Version first-party metadata before building wheels; never rename built wheels."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
import tomllib
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import cast

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

PACKAGES = frozenset(
    {
        "scopecat",
        "scopecat-server",
        "scopecat-instruments",
        "scopecat-quantum",
        "scopecat-app",
        "scopecat-tutorials",
        "scopecat-testkit",
    }
)


def pin(requirement: str, version: str) -> str:
    parsed = Requirement(requirement)
    if canonicalize_name(parsed.name) not in PACKAGES:
        return requirement
    if parsed.url:
        raise ValueError("First-party release dependencies must not use direct URLs")
    extras = f"[{','.join(sorted(parsed.extras))}]" if parsed.extras else ""
    marker = f"; {parsed.marker}" if parsed.marker else ""
    return f"{parsed.name}{extras}=={version}{marker}"


def stamp(project: Path, version: str) -> None:
    """Change only project version and runtime dependency tables in a source copy."""
    lines = project.read_text(encoding="utf-8").splitlines(keepends=True)
    section = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        if line.lstrip().startswith("["):
            section = line.strip()
        if section == "[project]" and re.match(r"version\s*=", line):
            lines[index] = f'version = "{version}"\n'
        dependency = (
            section == "[project]" and re.match(r"dependencies\s*=", line)
        ) or (
            section == "[project.optional-dependencies]"
            and "=" in line
            and not line.lstrip().startswith("#")
        )
        if dependency:
            end = index + 1
            while True:
                try:
                    table = cast(
                        "dict[str, object]", tomllib.loads("".join(lines[index:end]))
                    )
                    break
                except tomllib.TOMLDecodeError:
                    end += 1
                    if end > len(lines):
                        raise ValueError("Incomplete dependency table") from None
            key, values = next(iter(table.items()))
            if not isinstance(values, list) or not all(
                isinstance(x, str) for x in cast("list[object]", values)
            ):
                raise ValueError("Runtime dependencies must be arrays of strings")
            updated = [pin(value, version) for value in cast("list[str]", values)]
            lines[index:end] = [f"{key} = {json.dumps(updated)}\n"]
        index += 1
    project.write_text("".join(lines), encoding="utf-8")


def validate_dependencies(requirements: Sequence[str], version: str) -> None:
    for value in requirements:
        requirement = Requirement(value)
        if canonicalize_name(requirement.name) in PACKAGES and (
            requirement.url or str(requirement.specifier) != f"=={version}"
        ):
            raise ValueError(f"First-party dependency must select =={version}: {value}")


@contextmanager
def staged_project(package: Path, version: str | None) -> Generator[Path]:
    if version is None:
        yield package
        return
    with tempfile.TemporaryDirectory(prefix="scopecat-versioned-") as temporary:
        target = Path(temporary) / package.name
        shutil.copytree(
            package,
            target,
            ignore=shutil.ignore_patterns(
                ".venv",
                "__pycache__",
                "*.pyc",
                "dist",
                ".pytest_cache",
                ".ruff_cache",
            ),
        )
        stamp(target / "pyproject.toml", version)
        yield target
