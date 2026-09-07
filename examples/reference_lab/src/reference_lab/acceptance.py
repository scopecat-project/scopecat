"""Production responses for the shared, hardware-free reference-lab acceptance slice."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from pydantic import JsonValue
from scopecat.api.lab import LabClient
from scopecat.application.launch import LaunchRequest
from scopecat.daemon.client import DaemonClient
from scopecat_instruments import temperature_readout

from reference_lab.configuration import bootstrap_config
from reference_lab.launch import CATALOG, launch_provider
from reference_lab.parameters import CHANNEL_DELAY, Q1_CHANNEL_CALIBRATION
from reference_lab.workflows.ramsey_experiments import parallel_raw_ramsey
from reference_lab.workflows.temperature_diagnostic import (
    TemperatureDiagnosticIntent,
    temperature_diagnostic,
    temperature_diagnostic_procedure,
)

FIXTURE_TIME = datetime(2026, 9, 1, tzinfo=UTC)


def capture_acceptance_fixtures(
    lab: LabClient, client: DaemonClient
) -> dict[str, JsonValue]:
    """Caller owns a fresh isolated daemon; all device access uses its virtual lab."""
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
            Q1_CHANNEL_CALIBRATION.update(CHANNEL_DELAY.value(1.0)),
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
    diagnostic = diagnostic.model_copy(
        update={
            "items": tuple(
                record.model_copy(
                    update={
                        "run_id": "acceptance-diagnostic",
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
                for record in diagnostic.items
            ),
        }
    )
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
        "diagnostic": diagnostic.model_dump(mode="json"),
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
