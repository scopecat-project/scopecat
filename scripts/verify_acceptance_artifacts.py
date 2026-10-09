"""Validate an exact-commit acceptance build before reusing its browser assets."""

# ruff: noqa: S603, S607 -- fixed gh API argument lists, no shell

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import zipfile
from pathlib import Path
from typing import TypedDict, cast

from lab_tools.preview import read_preview


class Repository(TypedDict):
    full_name: str


class Run(TypedDict):
    repository: Repository
    head_repository: Repository
    head_sha: str
    path: str
    event: str
    status: str


class Job(TypedDict):
    name: str
    conclusion: str


class ArtifactRun(TypedDict):
    head_sha: str


class Artifact(TypedDict):
    id: int
    name: str
    expired: bool
    workflow_run: ArtifactRun


class Arguments(argparse.Namespace):
    commit: str = ""
    bundle: Path | None = None
    run_id: str = ""
    repository: str = ""


def verify_source(repository: str, run_id: str, commit: str) -> dict[str, object]:
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("Framework run ID must be a positive integer")
    base = f"repos/{repository}/actions/runs/{run_id}"

    def api(path: str, collection: str = "") -> object:
        options = (
            ["--paginate", "--jq", f".{collection}[] | @json"] if collection else []
        )
        output = subprocess.check_output(["gh", "api", *options, path], text=True)
        if collection:
            return [cast("object", json.loads(line)) for line in output.splitlines()]
        return cast("object", json.loads(output))

    run = cast("Run", api(base))
    if (
        run["repository"]["full_name"] != repository
        or run["head_repository"]["full_name"] != repository
        or run["head_sha"] != commit
        or run["path"] != ".github/workflows/acceptance.yml"
        or run["event"] != "workflow_dispatch"
        or run["status"] != "completed"
    ):
        raise ValueError(
            "Framework source must be a completed same-repository acceptance run "
            "at this exact commit"
        )
    jobs = cast("list[Job]", api(f"{base}/jobs?filter=all&per_page=100", "jobs"))
    if not any(job["name"] == "UI" and job["conclusion"] == "success" for job in jobs):
        raise ValueError("Framework source has no successful UI artifact producer")
    artifacts = [
        artifact
        for artifact in cast(
            "list[Artifact]", api(f"{base}/artifacts?per_page=100", "artifacts")
        )
        if artifact["name"] == "scopecat-framework" and not artifact["expired"]
    ]
    if len(artifacts) != 1 or artifacts[0]["workflow_run"]["head_sha"] != commit:
        raise ValueError("Framework source needs one unexpired matching artifact")
    return {
        "source_run": int(run_id),
        "source_commit": commit,
        "artifact_id": artifacts[0]["id"],
    }


def verify_bundle(bundle: Path, commit: str) -> dict[str, object]:
    manifest = read_preview(bundle / "preview.json")
    if manifest["commit"] != commit:
        raise ValueError(
            "Framework artifacts and browser checkout must use the same commit"
        )
    if "scopecat-ui.zip" not in manifest["files"]:
        raise ValueError("Framework artifact is missing its GUI")
    for name, digest in manifest["files"].items():
        with (bundle / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise ValueError(f"Framework checksum mismatch: {name}")
    with zipfile.ZipFile(bundle / "scopecat-ui.zip") as archive:
        identity = cast("object", json.loads(archive.read("build-info.json")))
    if not manifest.get("ui_version") or identity != {
        "source_commit": commit,
        "ui_version": manifest.get("ui_version"),
    }:
        raise ValueError("GUI build identity differs from the framework manifest")
    return {"source_commit": commit, "files": manifest["files"]}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--repository")
    args = parser.parse_args(namespace=Arguments())
    commit = args.commit
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        parser.error("commit must be a full SHA")
    if args.bundle is not None:
        result = verify_bundle(args.bundle, commit)
    elif args.run_id and args.repository:
        result = verify_source(args.repository, args.run_id, commit)
    else:
        parser.error("provide --bundle or --run-id and --repository")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
