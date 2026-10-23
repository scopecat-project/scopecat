"""Summarize OSV's native lockfile scan; findings are advisory, scan errors are not."""

from __future__ import annotations

import json
import sys
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
        "| Lockfile | Extracted packages | Affected packages | Advisory groups |",
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
            "they do not establish runtime reachability. Python workspace packages "
            "may be extracted as PyPI identities and need source-aware triage."
        ),
    ]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    print(summarize(cast("Report", json.loads(Path(sys.argv[1]).read_text()))), end="")
