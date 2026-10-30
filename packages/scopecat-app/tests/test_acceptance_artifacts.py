"""Browser-only qualification must execute, and must use matching immutable inputs."""

# ruff: noqa: S603 -- execute the maintained Ubuntu gate with fixture results

import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "verify_acceptance_artifacts", ROOT / "scripts/verify_acceptance_artifacts.py"
)
assert spec is not None and spec.loader is not None
verify = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify)
COMMIT = "a" * 40


@pytest.fixture
def bundle(tmp_path: Path) -> Path:
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


def test_exact_bundle_checks_every_declared_file(bundle: Path):
    assert verify.verify_bundle(bundle, COMMIT)["source_commit"] == COMMIT
    (bundle / "sample.whl").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        verify.verify_bundle(bundle, COMMIT)


@pytest.mark.parametrize(
    "failure",
    ["commit", "gui_commit", "gui_version", "missing_gui", "missing_wheel", "path"],
)
def test_reject_mixed_or_missing_artifacts(bundle: Path, failure):
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
    "CANDIDATE_ARTIFACTS",
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
    "candidate-artifacts": {"CANDIDATE_ARTIFACTS"},
    "native-distribution": {"NATIVE_DISTRIBUTION"},
    "service-lifecycle": {"SERVICE_LIFECYCLE"},
    "browser": {"UI", "BROWSER_TESTS"},
    "teaching": {"UI", "INSTALLED_ARTIFACTS"},
    "local-application": {"UI", "BROWSER_TESTS", "INSTALLED_ARTIFACTS"},
    "full": set(JOBS)
    - {"CANDIDATE_ARTIFACTS", "NATIVE_DISTRIBUTION", "SERVICE_LIFECYCLE"},
}


def gate(profile, results):
    workflow = (ROOT / ".github/workflows/acceptance.yml").read_text()
    job = yaml.safe_load(workflow)["jobs"]["gate"]
    assert job["if"] == "${{ always() }}"
    steps = [step for step in job["steps"] if "run" in step]
    assert len(steps) == 1
    step = steps[0]
    assert "if" not in step and not step.get("continue-on-error", False)
    assert set(job["needs"]) == {name.lower().replace("_", "-") for name in JOBS}
    assert step["env"]["PROFILE"] == "${{ inputs.profile }}"
    for name in JOBS:
        owner = name.lower().replace("_", "-")
        assert step["env"][f"{name}_RESULT"] in (
            "${{ needs[" + repr(owner) + "].result }}",
            "${{ needs." + owner + ".result }}",
        )
    script = step["run"]
    bash = shutil.which("bash")
    if os.name == "nt":
        git = shutil.which("git")
        assert git is not None, "Git for Windows is required to exercise the Bash gate"
        bash = next(
            (
                str(root / "bin/bash.exe")
                for root in Path(git).parents
                if (root / "bin/bash.exe").is_file()
            ),
            None,
        )
    assert bash is not None, "Bash is required to exercise the real workflow gate"
    return subprocess.run(
        [bash, "-e", "-c", script],
        env={
            **os.environ,
            "PROFILE": profile,
            **{f"{k}_RESULT": v for k, v in results.items()},
        },
        capture_output=True,
    )


@pytest.mark.parametrize("profile", SELECTED)
def test_gate_keeps_existing_profiles_and_requires_selected_jobs(profile):
    results = {
        job: "success" if job in SELECTED[profile] else "skipped" for job in JOBS
    }
    success = gate(profile, results)
    assert success.returncode == 0, success.stdout + success.stderr
    for job in JOBS:
        for invalid in (
            ["skipped", "failure", "cancelled"]
            if job in SELECTED[profile]
            else ["success", "failure"]
        ):
            assert gate(profile, {**results, job: invalid}).returncode != 0, (
                profile,
                job,
                invalid,
            )


def test_unknown_or_empty_selection_cannot_pass():
    assert gate("unknown", dict.fromkeys(JOBS, "skipped")).returncode != 0
    assert gate("browser", dict.fromkeys(JOBS, "skipped")).returncode != 0


def test_gate_ignores_display_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    workflow = yaml.safe_load((ROOT / ".github/workflows/acceptance.yml").read_text())
    for step in workflow["jobs"]["gate"]["steps"]:
        step["name"] = "A reader-facing label"
    path = tmp_path / ".github/workflows/acceptance.yml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump(workflow))
    monkeypatch.setitem(globals(), "ROOT", tmp_path)
    test_gate_keeps_existing_profiles_and_requires_selected_jobs("browser")


@pytest.mark.parametrize("profile", SELECTED)
def test_workflow_selects_exact_profile_jobs(profile):
    jobs = yaml.safe_load((ROOT / ".github/workflows/acceptance.yml").read_text())[
        "jobs"
    ]
    selected = set()
    for name, body in jobs.items():
        if name == "gate":
            continue
        condition = body.get("if")
        if condition is None:
            assert name == "browser-tests" and body["needs"] == "ui"
            condition = jobs["ui"]["if"]
        terms = re.split(r"\s*\|\|\s*", condition)
        assert all(
            re.fullmatch(r"inputs.profile\s*==\s*'[\w-]+'", term) for term in terms
        )
        enabled = any(term.split("==")[1].strip() == repr(profile) for term in terms)
        if enabled:
            selected.add(name.upper().replace("-", "_"))
    assert selected == SELECTED[profile]
    browser = jobs["browser-tests"]
    steps = [
        step for step in browser["steps"] if "BROWSER_SHARD" in step.get("env", {})
    ]
    assert len(steps) == 1
    assert '--project="journey-${BROWSER_SHARD}"' in steps[0]["run"]
    assert steps[0]["env"]["BROWSER_SHARD"] == "${{ matrix.shard }}"


def browser_verification_step(workflow):
    steps = yaml.safe_load(workflow)["jobs"]["browser-tests"]["steps"]
    downloads = [
        (index, step)
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/download-artifact@")
        and step.get("with", {}).get("name") == "scopecat-framework"
    ]
    checks = [
        (index, step)
        for index, step in enumerate(steps)
        if "verify_acceptance_artifacts.py" in step.get("run", "")
    ]
    extracts = [
        index
        for index, step in enumerate(steps)
        if "python -m zipfile" in step.get("run", "")
    ]
    assert len(downloads) == len(checks) == len(extracts) == 1
    download_index, download = downloads[0]
    check_index, check = checks[0]
    assert download_index < check_index < extracts[0]
    assert download["with"]["path"] == "dist/framework"
    assert not {"github-token", "run-id"} & download["with"].keys()
    for step in (download, check, steps[extracts[0]]):
        # These prerequisites must run on the successful path and propagate failure.
        assert "if" not in step and not step.get("continue-on-error", False)
    return check


def test_browser_rechecks_downloaded_identity_before_extraction(bundle: Path):
    workflow = (ROOT / ".github/workflows/acceptance.yml").read_text()
    step = browser_verification_step(workflow)
    command = shlex.split(step["run"])
    # Run the actual verifier and its workflow arguments in a disposable layout.
    # Only uv environment selection is substituted with this test's interpreter.
    assert command[:6] == ["uv", "run", "--locked", "--project", "../..", "python"]
    assert command[command.index("--commit") + 1] == "$GITHUB_SHA"
    root = bundle / "checkout"
    ui = root / "apps/ui"
    ui.mkdir(parents=True)
    scripts = root / "scripts"
    shutil.copytree(
        ROOT / "scripts", scripts, ignore=shutil.ignore_patterns("__pycache__")
    )
    target = root / "dist/framework"
    shutil.copytree(bundle, target, ignore=shutil.ignore_patterns("checkout"))
    for corrupted in (False, True):
        if corrupted:
            (target / "sample.whl").write_bytes(b"changed after download")
        result = subprocess.run(
            [
                sys.executable,
                *[
                    COMMIT if argument == "$GITHUB_SHA" else argument
                    for argument in command[6:]
                ],
            ],
            cwd=ui,
            capture_output=True,
            text=True,
        )
        assert (result.returncode == 0) is not corrupted, result.stdout + result.stderr


@pytest.mark.parametrize("change", ["disabled", "ignored-failure", "renamed"])
def test_browser_verification_contract_ignores_labels_but_rejects_bypass(change):
    workflow = yaml.safe_load((ROOT / ".github/workflows/acceptance.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["browser-tests"]["steps"]
        if "verify_acceptance_artifacts.py" in step.get("run", "")
    )
    if change == "disabled":
        step["if"] = "${{ false }}"
    elif change == "ignored-failure":
        step["continue-on-error"] = True
    else:
        for job in workflow["jobs"].values():
            for item in job.get("steps", []):
                item["name"] = "Renamed for readers"
    document = yaml.safe_dump(workflow)
    if change == "renamed":
        browser_verification_step(document)
    else:
        with pytest.raises(AssertionError):
            browser_verification_step(document)


@pytest.mark.parametrize("profile", SELECTED)
@pytest.mark.parametrize("shard", ["", "both", "1", "2"])
def test_browser_scope_preserves_default_and_integrated_profiles(profile, shard):
    import ast

    workflow = yaml.safe_load((ROOT / ".github/workflows/acceptance.yml").read_text())
    browser = workflow["jobs"]["browser-tests"]
    raw = browser["strategy"]["matrix"]["shard"]
    assert raw.startswith("${{ fromJSON(") and raw.endswith(") }}")
    expression = raw.removeprefix("${{ fromJSON(").removesuffix(") }}")
    inputs = {"inputs.profile": profile, "inputs.browser_shard": shard}
    # Evaluate the workflow's restricted string comparisons and &&/|| chains.
    # Unlike duplicating the selection logic, this checks its actual guards.
    selected = None
    for alternative in expression.split(" || "):
        terms = alternative.split(" && ")
        matches = True
        for condition in terms[:-1]:
            variable, expected = condition.split(" == ")
            matches = matches and inputs[variable] == ast.literal_eval(expected)
        if matches:
            selected = json.loads(ast.literal_eval(terms[-1]))
            break
    expected = [int(shard)] if profile == "browser" and shard in {"1", "2"} else [1, 2]
    assert selected == expected
    step = next(
        step for step in browser["steps"] if "BROWSER_FILES" in step.get("env", {})
    )
    assert (
        step["env"]["BROWSER_FILES"]
        == "${{ inputs.profile == 'browser' && inputs.browser_files || '' }}"
    )
