"""Explicit experiment relationships describe inputs without executing science."""

from dataclasses import replace

import pytest
import scopecat as sc
from scopecat.application.authoring import AuthorExperiment, AuthorExperiments
from scopecat.measurements.dataset import Dataset
from scopecat.records.analysis_actions import RunAnalysisActionsRequest
from scopecat.records.author_revision import AuthorRevisionRef
from ui_signal.analysis import (
    PeakResult,
    estimate_peak,
    verify_peak,
    verify_with_context,
)
from ui_signal.ordinary import signal

from scopecat_server.analysis_actions import describe_run_analyses

REVISION = AuthorRevisionRef(content_hash="sha256:" + "a" * 64)
REQUEST = RunAnalysisActionsRequest(
    run_id="run-original", workspace_id="original", code_revision=REVISION
)


def test_declared_signal_actions_keep_required_inputs_units_and_order() -> None:
    experiment = AuthorExperiment.from_declaration(
        signal, code_revision=REVISION, workspace_id="original"
    )
    definitions = {
        "ui_signal.analysis:estimate_peak": estimate_peak,
        "ui_signal.analysis:verify_peak": verify_peak,
    }
    result = describe_run_analyses(
        REQUEST,
        metadata=experiment.provenance,
        authors=AuthorExperiments((experiment,)),
        load_definition=definitions.__getitem__,
    )
    assert result.run_id == REQUEST.run_id
    assert result.code_revision == REVISION
    estimate, verification = result.actions
    assert estimate.title == "Estimate peak"
    assert estimate.inputs[0].default == 0.2
    assert estimate.inputs[0].kind == "number"
    assert verification.title == "Check expected peak"
    expected, tolerance = verification.inputs
    assert expected.name == "expected_frequency"
    assert expected.required and expected.kind == "quantity"
    assert tolerance.default == {"value": 50, "unit": "MHz"}
    assert not tolerance.required


@pytest.mark.parametrize(
    "field", ["author_workspace", "author_code_revision", "author_declaration"]
)
def test_foreign_run_identity_cannot_load_functions(field: str) -> None:
    experiment = AuthorExperiment.from_declaration(
        signal, code_revision=REVISION, workspace_id="original"
    )
    metadata = {**experiment.provenance, field: "other"}

    def unexpected_load(_reference: str) -> object:
        pytest.fail("mismatched source must not load functions")

    with pytest.raises(ValueError, match=r"(exact workspace and source|unavailable)"):
        describe_run_analyses(
            REQUEST,
            metadata=metadata,
            authors=AuthorExperiments((experiment,)),
            load_definition=unexpected_load,
        )


def test_old_declaration_has_no_guessed_actions() -> None:
    experiment = AuthorExperiment.from_declaration(
        replace(signal, analyses={}), code_revision=REVISION, workspace_id="original"
    )

    def unexpected_load(_reference: str) -> object:
        pytest.fail("no declaration must not discover functions")

    assert not describe_run_analyses(
        REQUEST,
        metadata=experiment.provenance,
        authors=AuthorExperiments((experiment,)),
        load_definition=unexpected_load,
    ).actions


def test_context_analysis_is_explained_without_invoking_it() -> None:
    experiment = AuthorExperiment.from_declaration(
        replace(
            signal, analyses={"Context check": "ui_signal.analysis:verify_with_context"}
        ),
        code_revision=REVISION,
        workspace_id="original",
    )
    result = describe_run_analyses(
        REQUEST,
        metadata=experiment.provenance,
        authors=AuthorExperiments((experiment,)),
        load_definition=lambda _reference: verify_with_context,
    )
    assert result.actions[0].unavailable_reason == (
        "This entry supports ordinary Dataset analysis functions only."
    )


def test_relationships_are_copied_and_references_are_explicit() -> None:
    values = {"Fit": "ui_signal.analysis:estimate_peak"}
    declared = replace(signal, analyses=values)
    values.clear()
    assert len(declared.analyses) == 1
    with pytest.raises(ValueError, match="module:function"):
        replace(signal, analyses={"Fit": "estimate_peak"})


@sc.analysis_function
def list_input(data: Dataset, *, values: list[float]) -> PeakResult:
    raise AssertionError("catalog reads must not execute analysis")


def test_non_form_input_is_unavailable_without_executing_analysis() -> None:
    experiment = AuthorExperiment.from_declaration(
        replace(signal, analyses={"List input": "example.analysis:list_input"}),
        code_revision=REVISION,
        workspace_id="original",
    )
    result = describe_run_analyses(
        REQUEST,
        metadata=experiment.provenance,
        authors=AuthorExperiments((experiment,)),
        load_definition=lambda _: list_input,
    )
    assert result.actions[0].unavailable_reason == (
        "values: input type is not supported in Runs; use Python."
    )


@pytest.mark.parametrize(
    ("failure", "status", "message"),
    [
        (
            ValueError("Retained execution environment is unavailable"),
            503,
            "Retained execution environment is unavailable",
        ),
        (OSError("author interpreter missing"), 503, "author interpreter missing"),
        (KeyError("saved revision missing"), 503, "saved revision missing"),
        (ValueError("invalid declaration payload"), 503, "invalid declaration payload"),
        (
            OSError("cannot read saved declaration"),
            503,
            "cannot read saved declaration",
        ),
    ],
)
def test_catalog_load_failures_preserve_details_without_guessing_repair(
    failure: Exception, status: int, message: str
) -> None:
    from unittest.mock import Mock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from scopecat_server.http.analysis_actions import analysis_actions_router

    read = Mock()
    read.return_value.request.metadata = {
        "author_workspace": REQUEST.workspace_id,
        "author_code_revision": REVISION.content_hash,
    }
    describe = Mock(side_effect=failure)
    app = FastAPI()
    app.include_router(analysis_actions_router(read, describe))
    response = TestClient(app).get("/api/v1/runs/run-original/analysis-actions")
    assert response.status_code == status
    detail = response.json()["detail"]
    assert detail.startswith("The saved analysis declarations could not be loaded.")
    assert f"Details: {failure}" in detail
    assert message in detail
    assert "Restore" not in detail
    describe.assert_called_once_with(REQUEST)


def test_catalog_timeout_does_not_suggest_unknown_analysis_publication() -> None:
    from unittest.mock import Mock

    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from scopecat_server.http.analysis_actions import analysis_actions_router

    read = Mock()
    read.return_value.request.metadata = {
        "author_workspace": REQUEST.workspace_id,
        "author_code_revision": REVISION.content_hash,
    }
    app = FastAPI()
    app.include_router(
        analysis_actions_router(
            read,
            Mock(side_effect=HTTPException(504, "Publication outcome may be unknown.")),
        )
    )
    response = TestClient(app).get("/api/v1/runs/run-original/analysis-actions")
    assert response.status_code == 504
    assert "No analysis was submitted" in response.json()["detail"]
    assert "Publication" not in response.json()["detail"]
