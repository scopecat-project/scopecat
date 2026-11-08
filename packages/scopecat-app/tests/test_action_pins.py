"""Workflow SHA pins must remain visible to Renovate's version updates."""

from pathlib import Path

import pytest
from scripts import check_action_pins as check

SHA = "a" * 40
ACTION = f"owner/action@{SHA}"


def workflow(uses: str) -> str:
    return f"jobs:\n  check:\n    steps:\n      - uses: {uses}\n"


@pytest.mark.parametrize("quote", ["", "'", '"'])
@pytest.mark.parametrize("version", ["v7", "v7.1", "v7.1.0", "7.1.0", "v1.0.0-rc.1"])
def test_version_comments_are_recognized(quote: str, version: str) -> None:
    source = workflow(f"{quote}{ACTION}{quote} # {version}")
    assert check.check_workflow(source, "ci.yml") == []


@pytest.mark.parametrize("comment", ["", " # pinned", " # latest", " # v1.2.3oops"])
@pytest.mark.parametrize("quote", ["", "'", '"'])
def test_missing_or_unusable_comments_fail(comment: str, quote: str) -> None:
    errors = check.check_workflow(
        workflow(f"{quote}{ACTION}{quote}{comment}"), "ci.yml"
    )
    assert len(errors) == 1
    assert errors[0].startswith(f"ci.yml:4: {ACTION} needs an inline version comment")


def test_reusable_workflow_pins_are_checked() -> None:
    source = f"jobs:\n  audit:\n    uses: owner/repo/.github/workflows/scan.yml@{SHA}\n"
    assert len(check.check_workflow(source, "ci.yml")) == 1
    assert check.check_workflow(source.rstrip() + " # v2.7.0\n", "ci.yml") == []


def test_comment_on_another_line_does_not_annotate_pin() -> None:
    source = workflow(ACTION) + "        # v7.1.0\n"
    assert len(check.check_workflow(source, "ci.yml")) == 1


def test_multiline_uses_requires_an_inline_pin_and_comment() -> None:
    source = workflow(f">- # v7.1.0\n          {ACTION}")
    assert len(check.check_workflow(source, "ci.yml")) == 1


@pytest.mark.parametrize(
    "uses", ["./.github/actions/local", "owner/action@v7", "docker://alpine@sha256:abc"]
)
def test_non_commit_references_are_outside_annotation_check(uses: str) -> None:
    assert check.check_workflow(workflow(uses), "ci.yml") == []


def test_shell_text_and_input_values_are_not_actions() -> None:
    source = (
        "jobs:\n  check:\n    steps:\n"
        f"      - run: |\n          uses: {ACTION}\n"
        f"        env:\n          uses: {ACTION}\n"
        f"      - uses: {ACTION} # v7.1.0\n"
        f"        with:\n          uses: {ACTION}\n"
    )
    assert check.check_workflow(source, "ci.yml") == []


def test_cli_checks_both_extensions_and_returns_failure(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    directory = tmp_path / ".github/workflows"
    directory.mkdir(parents=True)
    good = directory / "good.yml"
    bad = directory / "bad.yaml"
    good.write_text(workflow(f"{ACTION} # v7.1.0"), encoding="utf-8")
    bad.write_text(workflow(ACTION), encoding="utf-8")
    assert check.main(tmp_path) == 1
    assert ".github/workflows/bad.yaml:4:" in capsys.readouterr().out
    bad.write_text(workflow(f"{ACTION} # v7.1.0"), encoding="utf-8")
    assert check.main(tmp_path) == 0
    assert "checked in 2 workflows" in capsys.readouterr().out


def test_invalid_yaml_is_not_a_silent_pass(tmp_path: Path) -> None:
    directory = tmp_path / ".github/workflows"
    directory.mkdir(parents=True)
    (directory / "bad.yml").write_text("jobs: [", encoding="utf-8")
    assert check.main(tmp_path) == 1


def test_missing_workflows_are_not_a_silent_pass(tmp_path: Path) -> None:
    assert check.main(tmp_path) == 1


def test_repository_pins_are_annotated_and_quality_runs_checker() -> None:
    root = Path(__file__).resolve().parents[3]
    assert check.main(root) == 0
    for name in ("ci.yml", "acceptance.yml"):
        source = (root / ".github/workflows" / name).read_text(encoding="utf-8")
        assert "run: uv run --locked python scripts/check_action_pins.py" in source
