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


def gate(profile, results):
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
            terms = condition[1].split(" || ")
            assert all(
                re.fullmatch(r"inputs.profile == '[\w-]+'", term) for term in terms
            )
            enabled = any(term == f"inputs.profile == '{profile}'" for term in terms)
        else:
            assert name == "browser-tests" and "    needs: ui\n" in body
            enabled = "UI" in selected
        if enabled:
            selected.add(name.upper().replace("-", "_"))
    assert selected == SELECTED[profile]
    assert "shard: [1, 2]" in jobs["browser-tests"]
    assert "--project=journey-${{ matrix.shard }}" in jobs["browser-tests"]


def test_browser_rechecks_downloaded_identity_before_extraction():
    workflow = (ROOT / ".github/workflows/acceptance.yml").read_text()
    browser = workflow.split("  browser-tests:\n", 1)[1].split(
        "  installed-artifacts:\n", 1
    )[0]
    assert (
        browser.index("name: scopecat-framework")
        < browser.index("verify_acceptance_artifacts.py")
        < browser.index("python -m zipfile")
    )
    assert '--bundle ../../dist/framework --commit "$GITHUB_SHA"' in browser
    assert "github-token:" not in browser and "run-id:" not in browser
