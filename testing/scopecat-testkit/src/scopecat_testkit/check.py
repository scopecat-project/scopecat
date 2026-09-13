"""Explicit test tiers without changing the meaning of plain pytest."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import cast

import pytest


def select_files(root: Path, suite: str, shard: tuple[int, int] = (1, 1)) -> list[str]:
    config = tomllib.loads((root / "pyproject.toml").read_text())
    tool = cast("dict[str, object]", config["tool"])
    settings = cast("dict[str, object]", tool["scopecat-tests"])
    paths = cast("list[str]", settings["roots"])
    integration = cast("list[str]", settings.get("integration_paths", []))
    journeys = cast("list[str]", settings.get("journey_paths", []))
    weights = cast("dict[str, float]", settings.get("weights", {}))
    files = sorted(
        {
            p.relative_to(root).as_posix()
            for base in paths
            for pattern in ("test_*.py", "*_test.py")
            for p in (root / base).rglob(pattern)
        }
    )

    def matches(path: str, prefixes: list[str]) -> bool:
        return any(
            path == prefix or path.startswith(prefix + "/") for prefix in prefixes
        )

    def tier(path: str) -> str:
        if matches(path, journeys):
            return "journey"
        return "integration" if matches(path, integration) else "fast"

    selected = [
        p
        for p in files
        if suite == "full"
        or tier(p) == suite
        or (suite == "core" and tier(p) != "journey")
    ]
    index, count = shard
    buckets: list[list[str]] = [[] for _ in range(count)]
    loads = [0.0] * count
    for path in sorted(selected, key=lambda p: (-weights.get(p, 1.0), p)):
        target = min(range(count), key=lambda n: (loads[n], n))
        buckets[target].append(path)
        loads[target] += weights.get(path, 1.0)
    return sorted(buckets[index - 1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "suite", choices=("fast", "integration", "core", "journey", "full")
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--shard", default="1/1", help="One-based shard, for example 1/2"
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print selected files without collecting tests",
    )
    parser.add_argument("--report-dir", type=Path, default=Path(".test-results"))
    # Pass pytest options after --; tier selection remains explicit.
    raw = sys.argv[1:]
    separator = raw.index("--") if "--" in raw else len(raw)
    args = parser.parse_args(raw[:separator])
    extra = raw[separator + 1 :]
    try:
        index, count = (int(part) for part in args.shard.split("/"))
        if not 1 <= index <= count:
            raise ValueError
    except ValueError:
        parser.error("--shard requires 1 <= index <= count")
    root = args.root.resolve()
    files = select_files(root, args.suite, (index, count))
    if args.list:
        print("\n".join(files))
        return 0
    if not files:
        parser.error("selected suite/shard is empty; refusing implicit full collection")
    report = args.report_dir.resolve()
    report.mkdir(parents=True, exist_ok=True)
    prefix = f"{args.suite}-{index}-of-{count}"
    command = [
        "-c",
        str(root / "pyproject.toml"),
        "--rootdir",
        str(root),
        "-p",
        "scopecat_testkit.pytest_timing",
        "--durations=20",
        f"--junitxml={report / (prefix + '.xml')}",
        f"--timing-report={report / (prefix + '.json')}",
        *files,
        *extra,
    ]
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"],  # noqa: S607
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(
            ["git", "status", "--porcelain"],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    )
    for suffix in (".json", ".xml"):
        (report / (prefix + suffix)).unlink(missing_ok=True)
    (report / (prefix + "-selection.json")).write_text(
        json.dumps(
            {
                "suite": args.suite,
                "shard": [index, count],
                "revision": revision,
                "working_tree_dirty": dirty,
                "files": files,
                "pytest_args": extra,
            },
            indent=2,
        )
        + "\n"
    )
    # Avoid Windows command-line limits when a tier contains hundreds of files.
    os.chdir(root)
    return int(pytest.main(command))


if __name__ == "__main__":
    raise SystemExit(main())
