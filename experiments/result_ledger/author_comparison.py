"""Execute the same scientific scene from v1 and the derived-intent author API.

The old, self-contained experiment subtree is read from its immutable commit into
an isolated temporary directory. No production implementation is cherry-picked,
no user data is opened, and no timing comparison is made by this tool.
"""

from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
from math import prod
from pathlib import Path

FIRST_MILESTONE = "698fda92725e7359a016cea8af1a2816d2dd6100"
ROOT = Path(__file__).resolve().parents[2]
PREFIX = "experiments/result_ledger/"


def _git(*arguments: str) -> str:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("git is required to read the preserved first milestone")
    return subprocess.check_output(  # noqa: S603 - fixed git/read-only arguments
        [executable, "-C", str(ROOT), *arguments], text=True
    )


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def normalize(state: dict) -> dict:
    """Compare scientific identities through coordinates, not rewritten DB keys."""
    point_coordinates = {p["id"]: p["coordinates"] for p in state["points"]}
    groups = {}
    work_memberships = {}
    for snapshot in state["snapshots"]:
        work_memberships[snapshot["group_id"]] = sorted(
            [
                {
                    "points": sorted(
                        [point_coordinates[identity] for identity in unit["point_ids"]],
                        key=_canonical,
                    ),
                    "required_outputs": sorted(unit["required_outputs"]),
                    "output_contracts": unit["output_contracts"],
                }
                for unit in snapshot["units"]
            ],
            key=_canonical,
        )
        records = []
        for bundle in snapshot["bundles"]:
            commands = {
                block["provenance"]["command_id"] for block in bundle["outputs"]
            }
            assert len(commands) == 1, "simulated joint acquisition lost common source"
            assert all(
                block["provenance"]["provider"] == "synthetic-only"
                for block in bundle["outputs"]
            )
            for block in bundle["outputs"]:
                assert block["dims"][0] == "point", "comparator expects demo layout"
                width = prod(block["shape"][1:])
                for index, point_id in enumerate(block["point_ids"]):
                    start = index * width
                    records.append(
                        {
                            "coordinates": point_coordinates[point_id],
                            "output": block["id"],
                            "dims": block["dims"][1:],
                            "shape": block["shape"][1:],
                            "dtype": block["dtype"],
                            "unit": block["unit"],
                            "local_coordinates": block["coordinates"],
                            "values": block["values"][start : start + width],
                            "availability": block["availability"][
                                start : start + width
                            ],
                        }
                    )
        groups[snapshot["group_id"]] = sorted(records, key=_canonical)
    return {
        "point_coordinates": sorted(point_coordinates.values(), key=_canonical),
        "published_science": groups,
        "work_memberships": work_memberships,
        "logical_point_order": list(point_coordinates.values()),
        "publication_order": [e["payload"]["group_id"] for e in state["events"]],
        "closed_groups": sorted(g["id"] for g in state["groups"] if g["closed"]),
    }


def _run(repo: Path, directory: Path) -> dict:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    result = subprocess.run(  # noqa: S603 - local fixed module, fresh temp paths
        [
            sys.executable,
            "-m",
            "experiments.result_ledger",
            "demo",
            "--directory",
            str(directory),
        ],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return json.loads(result.stdout)


def declaration_lines(source: str, names: tuple[str, ...]) -> dict[str, int]:
    tree = ast.parse(source)
    found = {
        node.name: node.end_lineno - node.lineno + 1
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    }
    if set(found) != set(names):
        raise ValueError(f"declaration measurement no longer matches source: {names}")
    return found


def compare() -> dict:
    old_source = _git("show", FIRST_MILESTONE + ":" + PREFIX + "demo.py")
    with tempfile.TemporaryDirectory(prefix="result-author-comparison-") as temporary:
        root = Path(temporary)
        old_repo = root / "first-milestone"
        paths = _git(
            "ls-tree", "-r", "--name-only", FIRST_MILESTONE, "--", PREFIX
        ).splitlines()
        for relative in paths:
            if not relative.startswith(PREFIX) or ".." in Path(relative).parts:
                raise ValueError("unexpected experiment subtree path")
            destination = old_repo / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(_git("show", FIRST_MILESTONE + ":" + relative))
        first = _run(old_repo, root / "first-data")
        second = _run(ROOT, root / "second-data")
        assert normalize(first) == normalize(second), "same-scene scientific mismatch"
        return {
            "first_milestone": FIRST_MILESTONE,
            "current_revision": _git("rev-parse", "HEAD").strip(),
            "current_dirty": bool(_git("status", "--porcelain").strip()),
            "same_science": True,
            "first_counts": first["counts"],
            "second_counts": second["counts"],
            "second_author_source_lines": declaration_lines(
                (ROOT / PREFIX / "demo.py").read_text(), ("declaration",)
            ),
            "first_author_source_lines": declaration_lines(
                old_source, ("point", "unit", "declaration")
            ),
            "limits": [
                (
                    "Same simulated 2D/waveform/partial/adaptive scene; "
                    "not all production features"
                ),
                "Derived storage identities and provenance hashes intentionally differ",
                "Source lines are descriptive, not the acceptance criterion",
                (
                    "No timing claim; baseline production DSL comparison "
                    "is a separate real-core test"
                ),
            ],
        }


if __name__ == "__main__":
    print(json.dumps(compare(), indent=2))
