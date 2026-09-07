"""Production responses for the shared, hardware-free reference-lab acceptance slice."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Protocol, cast

from pydantic import JsonValue
from scopecat.api.lab import LabClient
from scopecat.application.launch import LaunchPreview, LaunchRequest
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import MeasurementPreview
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.measurement_recording import measurement_record_content_hash
from scopecat_instruments import temperature_readout

from reference_lab.configuration import bootstrap_config
from reference_lab.launch import CATALOG, launch_provider
from reference_lab.parameters import CHANNEL_DELAY, Q1_CHANNEL_CALIBRATION
from reference_lab.workflows.coherent_ramsey import coherent_ramsey
from reference_lab.workflows.ramsey_experiments import parallel_raw_ramsey
from reference_lab.workflows.temperature_diagnostic import (
    TemperatureDiagnosticIntent,
    temperature_diagnostic,
    temperature_diagnostic_procedure,
)


class _ArrowColumn(Protocol):
    def to_pylist(self) -> list[object]: ...


class _ArrowProjection(Protocol):
    def column(self, name: str) -> _ArrowColumn: ...


FIXTURE_TIME = datetime(2026, 9, 1, tzinfo=UTC)


def capture_acceptance_fixtures(
    lab: LabClient, client: DaemonClient
) -> dict[str, JsonValue]:
    """Caller owns a fresh isolated daemon; all device access uses its virtual lab."""
    setting_preview = launch_provider(
        lab, LaunchRequest(action="preview", experiment="channel-timing", version="1")
    )
    assert isinstance(setting_preview, LaunchPreview)
    assert setting_preview.preflight is not None
    config = bootstrap_config()
    active = lab.config.active()
    launch_preview = launch_provider(
        lab,
        LaunchRequest(
            action="preview",
            experiment="temperature",
            version="1",
        ),
    )
    diagnostic_run = lab.run(temperature_diagnostic(), config=config)
    assert diagnostic_run.status == "completed"
    assert lab.config.active() == active
    diagnostic = client.measurement_preview(diagnostic_run.id)
    assert diagnostic.items == diagnostic_run.measurements().records

    coherent = coherent_ramsey()
    coherent_run = lab.run(coherent, config=config)
    assert coherent_run.status == "completed"
    coherent_data = coherent_run.measurements()
    coherent_preview = client.measurement_preview(coherent_run.id, limit=4)
    assert coherent_preview.items == coherent_data.records
    mean_id = coherent_data[coherent.output.iq_mean].id
    assert next(
        variable
        for variable in coherent_data.schema.variables
        if variable.id == mean_id
    ).dims == ("point",)
    expected_values: list[dict[str, float]] = []
    for record in coherent_data.records:
        value = record.observables[mean_id]
        assert isinstance(value, MeasurementScalar) and value.dtype == "complex128"
        assert isinstance(value.value, complex)
        expected_values.append({"real": value.value.real, "imag": value.value.imag})
        restored = type(record).model_validate_json(record.model_dump_json())
        assert measurement_record_content_hash(
            restored
        ) == measurement_record_content_hash(record)
    assert any(value["imag"] != 0 for value in expected_values)
    assert len({(value["real"], value["imag"]) for value in expected_values}) > 1
    # Keep this source lazy: the previously inspected dataset has loaded records.
    table = cast(
        "_ArrowProjection",
        coherent_run.measurements().project({"iq_mean": mean_id}).to_arrow(),  # pyright: ignore[reportUnknownMemberType]
    )
    assert table.column("iq_mean").to_pylist() == expected_values

    before = client.list_runs()
    with lab.review(
        temperature_diagnostic(), config=config, name="Temperature diagnostic"
    ) as review:
        inspection = review.session
    assert client.list_runs() == before

    invocation = parallel_raw_ramsey()
    source = lab.run(invocation, config=config, name="Reference lab acceptance source")
    analysis = (
        source.analysis("Channel timing review")
        .result()
        .propose(
            "q1-channel-delay",
            Q1_CHANNEL_CALIBRATION[CHANNEL_DELAY].update(1.0),
            reason="align q1 acquisition with the shared readout window",
        )
        .save()
    )
    candidate = analysis.candidate_config()
    candidate_run = lab.run(invocation, config=candidate, name="Candidate verification")
    assert candidate_run.status == "completed"
    candidate_source = candidate_run.snapshot.config_source
    assert (
        candidate_source is not None and candidate_source.kind == "analysis_candidate"
    )
    assert candidate_source.proposal_id == candidate.proposal_id
    lab.config.accept(
        candidate, actor="acceptance-operator", note="Verified in virtual lab"
    )
    reviewed = client.parameter_proposals(source.id)
    lab.config.set_default(active.config)
    schema = source.measurements().schema

    with lab.instruments.open(temperature_readout("mixing-chamber")):
        procedure = lab.procedures.start(
            temperature_diagnostic_procedure,
            TemperatureDiagnosticIntent(initial_config=config),
            request_key="acceptance-resource-wait",
        )
        wait = procedure.snapshot.resource_wait
        assert wait is not None
        resources = client.get_run(wait.run_id).resources
        assert resources[0].status == "blocked"
        procedure.cancel(
            actor="acceptance-operator", reason="Cancel waiting diagnostic"
        )
        cancelled = client.get_run(wait.run_id).snapshot.outcome
        assert cancelled is not None and cancelled.result == "cancelled"
        assert client.measurement_preview(wait.run_id).items == ()

    # Normalize only capture metadata at explicit production-model fields. Do not
    # rewrite scientific values, arbitrary strings, content hashes or lineage.
    diagnostic = _normalize_preview(diagnostic, "acceptance-diagnostic")
    coherent_preview = _normalize_preview(coherent_preview, "acceptance-coherent")
    inspection = inspection.model_copy(
        update={
            "session_id": "acceptance-inspection",
            "created_at": FIXTURE_TIME,
            "updated_at": FIXTURE_TIME,
            "latest_result": inspection.latest_result.model_copy(
                update={"completed_at": FIXTURE_TIME}
            )
            if inspection.latest_result
            else None,
        }
    )
    reviewed = reviewed.model_copy(
        update={
            "run_id": "acceptance-source",
            "items": tuple(
                item.model_copy(
                    update={
                        "proposal": item.proposal.model_copy(
                            update={
                                "source_run_id": "acceptance-source",
                                "analysis_record_id": "acceptance-analysis",
                                "proposed_at": FIXTURE_TIME,
                            }
                        ),
                        "approval": item.approval.model_copy(
                            update={
                                "run_id": "acceptance-source",
                                "approved_at": FIXTURE_TIME,
                            }
                        )
                        if item.approval
                        else None,
                    }
                )
                for item in reviewed.items
            ),
        }
    )
    cancelled = cancelled.model_copy(
        update={"run_id": "acceptance-waiting", "finished_at": FIXTURE_TIME}
    )
    return {
        "launch_catalog": CATALOG.model_dump(mode="json"),
        "launch_preview": launch_preview.model_dump(mode="json"),
        "planned_settings": setting_preview.preflight.stages[0].model_dump(
            mode="json",
            include={
                "planned_settings",
                "planned_setting_limit",
                "planned_settings_truncated",
                "selected_points",
            },
        ),
        "diagnostic": diagnostic.model_dump(mode="json"),
        "coherent_scalar": coherent_preview.model_dump(mode="json"),
        "inspection": inspection.model_dump(mode="json"),
        "reviewed_candidate": reviewed.model_dump(mode="json"),
        "entity_analysis": schema.model_dump(mode="json"),
        "resource_waiting": [
            item.model_copy(
                update={
                    "blocked_by": item.blocked_by.model_copy(
                        update={"owner_id": "acceptance-session"}
                    )
                    if item.blocked_by
                    else None,
                }
            ).model_dump(mode="json")
            for item in resources
        ],
        "resource_cancelled": cancelled.model_dump(mode="json"),
    }


def acceptance_json(fixtures: dict[str, JsonValue]) -> str:
    return json.dumps(fixtures, indent=2, sort_keys=True, allow_nan=False) + "\n"


def _normalize_preview(preview: MeasurementPreview, run_id: str) -> MeasurementPreview:
    return preview.model_copy(
        update={
            "items": tuple(
                record.model_copy(
                    update={
                        "run_id": run_id,
                        "acquisition_evidence": record.acquisition_evidence.model_copy(
                            update={
                                "events": tuple(
                                    event.model_copy(
                                        update={
                                            "started_at": FIXTURE_TIME,
                                            "completed_at": FIXTURE_TIME,
                                        }
                                    )
                                    for event in record.acquisition_evidence.events
                                ),
                            }
                        ),
                    }
                )
                for record in preview.items
            ),
        }
    )
