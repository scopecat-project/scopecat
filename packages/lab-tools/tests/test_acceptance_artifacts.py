"""Browser-only qualification must execute, and must use matching immutable inputs."""

# ruff: noqa: S603, S607 -- execute the maintained Ubuntu gate with fixture results

import hashlib
import importlib.util
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "verify_acceptance_artifacts", ROOT / "scripts/verify_acceptance_artifacts.py"
)
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
COMMIT = "a" * 40


@pytest.fixture
def bundle(tmp_path):
    with zipfile.ZipFile(tmp_path / "scopecat-ui.zip", "w") as archive:
        archive.writestr(
            "build-info.json",
            json.dumps({"source_commit": COMMIT, "ui_version": "0.1.0"}),
        )
    (tmp_path / "sample.whl").write_bytes(b"wheel fixture")
    manifest = {
        "format": 1,
        "commit": COMMIT,
        "ui_version": "0.1.0",
        "packages": {},
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in tmp_path.iterdir()
        },
    }
    (tmp_path / "preview.json").write_text(json.dumps(manifest))
    return tmp_path


def test_exact_bundle_checks_every_declared_file(bundle):
    assert verify.verify_bundle(bundle, COMMIT)["source_commit"] == COMMIT
    (bundle / "sample.whl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        verify.verify_bundle(bundle, COMMIT)


@pytest.mark.parametrize(
    "failure",
    ["commit", "gui_commit", "gui_version", "missing_gui", "missing_wheel", "path"],
)
def test_reject_mixed_or_missing_artifacts(bundle, failure):
    path = bundle / "preview.json"
    manifest = json.loads(path.read_text())
    if failure == "commit":
        manifest["commit"] = "b" * 40
    elif failure.startswith("gui_"):
        with zipfile.ZipFile(bundle / "scopecat-ui.zip", "w") as archive:
            archive.writestr(
                "build-info.json",
                json.dumps(
                    {
                        "source_commit": "b" * 40
                        if failure == "gui_commit"
                        else COMMIT,
                        "ui_version": "wrong" if failure == "gui_version" else "0.1.0",
                    }
                ),
            )
        manifest["files"]["scopecat-ui.zip"] = hashlib.sha256(
            (bundle / "scopecat-ui.zip").read_bytes()
        ).hexdigest()
    elif failure == "missing_gui":
        del manifest["files"]["scopecat-ui.zip"]
    elif failure == "missing_wheel":
        (bundle / "sample.whl").unlink()
    else:
        manifest["files"]["../outside"] = "a" * 64
    path.write_text(json.dumps(manifest))
    with pytest.raises((ValueError, FileNotFoundError)):
        verify.verify_bundle(bundle, COMMIT)


@pytest.fixture
def source(monkeypatch):
    run = {
        "repository": {"full_name": "owner/repo"},
        "head_repository": {"full_name": "owner/repo"},
        "head_sha": COMMIT,
        "path": ".github/workflows/acceptance.yml",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "failure",
    }
    jobs = [{"name": "UI", "conclusion": "success"}]
    artifacts = [
        {
            "id": 123,
            "name": "scopecat-framework",
            "expired": False,
            "workflow_run": {"head_sha": COMMIT},
        }
    ]

    def api(command, **kwargs):
        path = command[-1]
        if "/jobs?" in path or "/artifacts?" in path:
            assert "--paginate" in command
            result = jobs if "/jobs?" in path else artifacts
            return "\n".join(json.dumps(item) for item in result)
        return json.dumps(run)

    monkeypatch.setattr(verify.subprocess, "check_output", api)
    return run, jobs, artifacts


def test_failed_journey_can_reuse_successful_build(source):
    assert verify.verify_source("owner/repo", "12", COMMIT)["artifact_id"] == 123


@pytest.mark.parametrize(
    "failure",
    [
        "repository",
        "fork",
        "commit",
        "workflow",
        "event",
        "running",
        "producer_failed",
        "producer_skipped",
        "producer_missing",
        "expired",
        "missing",
        "ambiguous",
        "artifact_commit",
        "invalid_id",
    ],
)
def test_source_rejects_unqualified_inputs(source, failure):
    run, jobs, artifacts = source
    if failure == "repository":
        run["repository"]["full_name"] = "other/repo"
    elif failure == "fork":
        run["head_repository"]["full_name"] = "other/repo"
    elif failure == "commit":
        run["head_sha"] = "b" * 40
    elif failure == "workflow":
        run["path"] = ".github/workflows/ci.yml"
    elif failure == "event":
        run["event"] = "pull_request"
    elif failure == "running":
        run["status"] = "in_progress"
    elif failure == "producer_failed":
        jobs[0]["conclusion"] = "failure"
    elif failure == "producer_skipped":
        jobs[0]["conclusion"] = "skipped"
    elif failure == "producer_missing":
        jobs.clear()
    elif failure == "expired":
        artifacts[0]["expired"] = True
    elif failure == "missing":
        artifacts.clear()
    elif failure == "ambiguous":
        artifacts.append(artifacts[0].copy())
    elif failure == "artifact_commit":
        artifacts[0]["workflow_run"]["head_sha"] = "b" * 40
    with pytest.raises(ValueError, match="Framework"):
        verify.verify_source(
            "owner/repo", "../12" if failure == "invalid_id" else "12", COMMIT
        )


JOBS = [
    "PUBLIC_PREVIEW",
    "NATIVE_DISTRIBUTION",
    "SERVICE_LIFECYCLE",
    "INSTALLED_ARTIFACTS",
    "BROWSER_TESTS",
    "PYTHON_TESTS",
    "BENCHMARK_SMOKE",
    "PYTHON_QUALITY",
    "UI",
    "DOCS",
]
SELECTED = {
    "public-preview": {"PUBLIC_PREVIEW"},
    "native-distribution": {"NATIVE_DISTRIBUTION"},
    "service-lifecycle": {"SERVICE_LIFECYCLE"},
    "browser": {"UI", "BROWSER_TESTS"},
    "local-application": {"UI", "BROWSER_TESTS", "INSTALLED_ARTIFACTS"},
    "full": set(JOBS) - {"PUBLIC_PREVIEW", "NATIVE_DISTRIBUTION", "SERVICE_LIFECYCLE"},
}


def gate(profile, results, run_id=""):
    workflow = (ROOT / ".github/workflows/acceptance.yml").read_text()
    script = workflow.split("      - name: Require successful jobs\n", 1)[1].split(
        "        run: |\n", 1
    )[1]
    script = "\n".join(line[10:] for line in script.splitlines())
    return subprocess.run(
        ["bash", "-e", "-c", script],
        env={
            **os.environ,
            "PROFILE": profile,
            "FRAMEWORK_RUN_ID": run_id,
            **{f"{k}_RESULT": v for k, v in results.items()},
        },
        capture_output=True,
    ).returncode


@pytest.mark.parametrize("profile", SELECTED)
def test_gate_keeps_existing_profiles_and_requires_selected_jobs(profile):
    results = {
        job: "success" if job in SELECTED[profile] else "skipped" for job in JOBS
    }
    assert gate(profile, results) == 0
    for job in JOBS:
        for invalid in (
            ["skipped", "failure", "cancelled"]
            if job in SELECTED[profile]
            else ["success", "failure"]
        ):
            assert gate(profile, {**results, job: invalid}) != 0, (
                profile,
                job,
                invalid,
            )
    assert (gate(profile, results, "123") == 0) == (profile == "browser")


def test_unknown_or_empty_selection_cannot_pass():
    assert gate("unknown", dict.fromkeys(JOBS, "skipped")) != 0
    assert gate("browser", dict.fromkeys(JOBS, "skipped")) != 0


@pytest.mark.parametrize("profile", SELECTED)
def test_workflow_selects_exact_profile_jobs(profile):
    # Read the actual job conditions, including the implicit successful-ui
    # dependency used by browser-tests; no duplicated selector implementation.
    import re

    workflow = (ROOT / ".github/workflows/acceptance.yml").read_text()
    jobs = dict(
        re.findall(
            r"(?ms)^  ([\w-]+):\n(.*?)(?=^  [\w-]+:|\Z)",
            workflow.split("\njobs:\n", 1)[1],
        )
    )
    selected = set()
    for name, body in jobs.items():
        if name == "gate":
            continue
        condition = re.search(r"^    if: (.*)$", body, re.M)
        if condition:
            terms = [term.split(" && ") for term in condition[1].split(" || ")]
            assert all(
                re.fullmatch(r"inputs.profile == '[\w-]+'", atom)
                or atom == "inputs.framework_run_id == ''"
                for term in terms
                for atom in term
            )
            enabled = any(
                all(
                    atom
                    in {
                        f"inputs.profile == '{profile}'",
                        "inputs.framework_run_id == ''",
                    }
                    for atom in term
                )
                for term in terms
            )
        else:
            assert name == "browser-tests" and "    needs: ui\n" in body
            enabled = "UI" in selected
        if enabled:
            selected.add(name.upper().replace("-", "_"))
    assert selected == SELECTED[profile]
    assert "shard: [1, 2]" in jobs["browser-tests"]
    assert "--project=journey-${{ matrix.shard }}" in jobs["browser-tests"]


def test_browser_reuse_input_cannot_start_publication():
    workflow = (ROOT / ".github/workflows/acceptance.yml").read_text()
    job = workflow.split("  public-preview:\n", 1)[1].split(
        "  native-distribution:\n", 1
    )[0]
    assert job.startswith(
        "    if: inputs.profile == 'public-preview' && inputs.framework_run_id == ''\n"
    )
