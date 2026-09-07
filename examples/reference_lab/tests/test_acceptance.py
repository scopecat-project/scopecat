"""The committed shared fixture must remain a real Python/HTTP lab result."""

from __future__ import annotations

import json
from typing import Protocol, cast

from pydantic import JsonValue, TypeAdapter
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.reviews import ReviewSessionView
from scopecat.daemon.views import (
    MeasurementPreview,
    ParameterProposalPage,
    RunResourceView,
)
from scopecat.kernel.run_outcome import RunOutcome
from scopecat.records.measurement import MeasurementDatasetSchema, MeasurementScalar

from reference_lab.acceptance import acceptance_json, capture_acceptance_fixtures
from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT

FIXTURE = EXAMPLE_ROOT / "fixtures" / "acceptance.json"


class _Daemon(Protocol):
    url: str


def test_shared_fixture_is_current_python_and_http_behavior(
    reference_lab_daemon: _Daemon,
) -> None:
    with (
        create_application(EXAMPLE_ROOT).connect(reference_lab_daemon.url) as lab,
        DaemonClient(reference_lab_daemon.url) as client,
    ):
        captured = capture_acceptance_fixtures(lab, client)
    assert acceptance_json(captured) == FIXTURE.read_text()


def test_shared_fixture_retains_diagnostic_review_and_entity_contracts() -> None:
    fixture = cast("dict[str, JsonValue]", json.loads(FIXTURE.read_text()))
    diagnostic = MeasurementPreview.model_validate(fixture["diagnostic"])
    [record] = diagnostic.items
    temperature = record.observables["temperature"]
    assert isinstance(temperature, MeasurementScalar)
    assert temperature.value == 0.02 and temperature.unit == "K"
    assert record.acquisition_evidence.events[0].instrument_id == "mixing-chamber"
    assert (
        ReviewSessionView.model_validate(fixture["inspection"]).latest_result
        is not None
    )
    [reviewed] = ParameterProposalPage.model_validate(
        fixture["reviewed_candidate"]
    ).items
    assert reviewed.proposal.id == "q1-channel-delay"
    assert reviewed.approval is not None
    assert reviewed.approval.actor == "acceptance-operator"
    schema = MeasurementDatasetSchema.model_validate(fixture["entity_analysis"])
    [entity_axis] = [
        dimension for dimension in schema.dimensions if dimension.kind == "entity"
    ]
    assert entity_axis.index is not None
    assert [entity.id for entity in entity_axis.index.values] == ["q0", "q1"]
    [waiting] = TypeAdapter(tuple[RunResourceView, ...]).validate_python(
        fixture["resource_waiting"]
    )
    assert waiting.status == "blocked" and waiting.blocked_by is not None
    assert (
        RunOutcome.model_validate(fixture["resource_cancelled"]).result == "cancelled"
    )
