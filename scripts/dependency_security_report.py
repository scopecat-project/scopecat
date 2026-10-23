"""Summarize OSV's native lockfile scan; findings are advisory, scan errors are not."""

from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import NotRequired, TypedDict, cast

LOCKFILES = ("uv.lock", "apps/scopecat-ui/pnpm-lock.yaml")


class Package(TypedDict):
    name: str
    version: str


class Vulnerability(TypedDict):
    id: str


class Entry(TypedDict):
    package: Package
    vulnerabilities: NotRequired[list[Vulnerability]]
    groups: NotRequired[list[object]]


class Source(TypedDict):
    path: str


class Result(TypedDict):
    source: Source
    packages: list[Entry]


class Report(TypedDict):
    results: list[Result]


class LockedPackage(TypedDict):
    name: str
    version: str
    source: dict[str, str]


def workspace_config(lockfile: Path) -> str:
    """Exclude exact local-source identities with native OSV overrides."""
    packages = cast(
        "list[LockedPackage]", tomllib.loads(lockfile.read_text())["package"]
    )
    lines = ["# Generated from uv.lock source metadata; not vulnerability exceptions."]
    for package in packages:
        source = package["source"]
        if "virtual" in source:
            continue
        if "editable" not in source and "directory" not in source:
            continue
        lines += [
            "",
            "[[PackageOverrides]]",
            f"name = {json.dumps(package['name'])}",
            f"version = {json.dumps(package['version'])}",
            'ecosystem = "PyPI"',
            "ignore = true",
            'reason = "Local workspace source, not the same-named PyPI distribution"',
        ]
    return "\n".join(lines) + "\n"


def summarize(report: Report) -> str:
    sources = report.get("results", [])
    lines = [
        "## Locked dependency security report",
        "",
        (
            "Advisory baseline; this is not a clean-security gate. "
            "Review findings before dependency merges."
        ),
        "",
        "| Lockfile | Audited packages | Affected packages | Advisory groups |",
        "| --- | ---: | ---: | ---: |",
    ]
    findings: list[str] = []
    for lockfile in LOCKFILES:
        matching = [
            source
            for source in sources
            if source["source"]["path"].replace("\\", "/").endswith("/" + lockfile)
            or source["source"]["path"] == lockfile
        ]
        if len(matching) != 1 or not matching[0].get("packages"):
            raise ValueError(f"Missing or empty scanner inventory: {lockfile}")
        packages = matching[0]["packages"]
        affected = [p for p in packages if p.get("vulnerabilities")]
        groups = sum(len(p.get("groups", [])) for p in affected)
        lines.append(f"| {lockfile} | {len(packages)} | {len(affected)} | {groups} |")
        for entry in affected:
            package = entry["package"]
            ids = sorted(v["id"] for v in entry.get("vulnerabilities", []))
            links = ", ".join(f"[{v}](https://osv.dev/vulnerability/{v})" for v in ids)
            findings.append(f"- {package['name']} {package['version']}: {links}")
    lines += ["", *findings] if findings else ["", "No known vulnerabilities returned."]
    lines += [
        "",
        (
            "Counts include development, optional and transitive locked packages; "
            "they do not establish runtime reachability. Local Python workspace "
            "identities "
            "are excluded using native OSV overrides derived from uv.lock sources."
        ),
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    _ = parser.add_argument("command", choices=("config", "report"))
    _ = parser.add_argument("path", type=Path)
    args = parser.parse_args()
    path = cast("Path", args.path)
    if cast("str", args.command) == "config":
        print(workspace_config(path), end="")
    else:
        print(summarize(cast("Report", json.loads(path.read_text()))), end="")
