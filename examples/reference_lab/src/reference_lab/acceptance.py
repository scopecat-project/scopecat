"""Production responses for the shared, hardware-free reference-lab acceptance slice."""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import UTC, datetime
from typing import Protocol, cast

import scopecat as sc
from pydantic import JsonValue
from scopecat.api.lab import LabClient
from scopecat.application.author_project import AuthorProject
from scopecat.application.launch import LaunchCatalog, LaunchPreview
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import MeasurementPreview
from scopecat.planning.preflight import ExactQuantity, summarize_preflight
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.measurement_recording import measurement_record_content_hash
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    ScientificSelection,
)
from scopecat_instruments import temperature_readout

from reference_lab.configuration import initial_parameters
from reference_lab.parameters import QubitParameters
from reference_lab.workflows.coherent_ramsey import coherent_ramsey
from reference_lab.workflows.ramsey_experiments import parallel_raw_ramsey
from reference_lab_authors.temperature_diagnostic import (
    TemperatureDiagnosticIntent,
    temperature_diagnostic,
    temperature_diagnostic_procedure,
)


class _ArrowColumn(Protocol):
    def to_pylist(self) -> list[object]: ...


class _ArrowProjection(Protocol):
    def column(self, name: str) -> _ArrowColumn: ...


FIXTURE_TIME = datetime(2026, 9, 1, tzinfo=UTC)
FIXTURE_WORKSPACE = "acceptance-source"


def checked_launch_preview(
    client: DaemonClient, request: LaunchRequest
) -> LaunchPreview:
    health = client.health()
    with AuthorProject(
        client.base_url, workspace_id=health.author_workspaces[health.project_root]
    ) as authors:
        catalog = authors.catalog()
        entry = next(item for item in catalog.entries if item.id == request.experiment)
        preview = authors.preview(request.model_copy(update={"version": entry.version}))
    assert preview.manual_state is not None
    assert preview.workspace_id == request.workspace_id
    assert preview.code_revision is not None
    assert preview.manual_state.binding.code_revision == preview.code_revision
    # UI shape/science fixtures are not executable permissions. Normalize only
    # process/environment-dependent source identity, compute IDs and retained event ID;
    # real admission journeys always use the original complete server response.
    revision = AuthorRevisionRef(content_hash="sha256:" + "0" * 64)
    inspection = preview.inspection
    if inspection is not None:
        inspection = inspection.model_copy(
            update={
                "computes": tuple(
                    replace(compute, implementation=f"python:fixture:{compute.id}")
                    for compute in inspection.computes
                ),
            }
        )
    return preview.model_copy(
        update={
            "workspace_id": FIXTURE_WORKSPACE,
            "inspection": inspection,
            "request_hash": "sha256:" + "0" * 64,
            "definition_hash": "sha256:" + "0" * 64,
            "code_revision": revision,
            "manual_state": preview.manual_state.model_copy(
                update={
                    "event_id": 1,
                    "binding": preview.manual_state.binding.model_copy(
                        update={
                            "code_revision": revision,
                            "request_hash": "sha256:" + "0" * 64,
                        }
                    ),
                }
            ),
        }
    )


def capture_acceptance_fixtures(
    lab: LabClient, client: DaemonClient
) -> dict[str, JsonValue]:
    """Caller owns a fresh isolated daemon; all device access uses its virtual lab."""
    health = client.health()
    workspace_id = health.author_workspaces[health.project_root]
    with AuthorProject(client.base_url, workspace_id=workspace_id) as authors:
        current_catalog = authors.catalog()
    assert current_catalog.workspace_id == workspace_id
    # The shared UI fixture retains the entityless physical diagnostic.
    # Source-derived versions are checked by admission tests, not this shape fixture.
    catalog = LaunchCatalog(
        workspace_id=FIXTURE_WORKSPACE,
        entries=tuple(
            entry.model_copy(update={"version": "sha256:" + "0" * 64})
            for entry in current_catalog.entries
            if entry.id
            in {
                "reference_lab.temperature_diagnostic",
            }
        ),
    )
    registry = lab.config.registry()
    setup = lab.setup.get("initial")
    content = initial_parameters()
    parameters = lab.parameters.save(
        name="acceptance-parameters",
        catalog=content.catalog,
        parameters=content.parameters,
    )
    resolved = lab.parameters.resolve(parameters, setup=setup)
    selection = ScientificSelection(
        configuration=ParameterConfiguration(ref=parameters.ref, setup=setup.ref)
    )
    # Inspect the actual compiled experiment, without the retired launcher shell.
    setting_preview = summarize_preflight(
        lab.preview_invocation(
            parallel_raw_ramsey.build(),
            config=resolved.config,
            config_source=resolved.config_source,
        ),
        stage_id="source",
        label="Ramsey source",
        configuration="selected_context",
        config_content_hash=resolved.config_source.content_hash,
        configuration_meaning="Explicit parameter and setup revisions",
        executions=ExactQuantity(value=1, unit="runs", basis="One source acquisition"),
    )
    config = resolved.config
    launch_preview = checked_launch_preview(
        client,
        LaunchRequest(
            workspace_id=workspace_id,
            action="preview",
            experiment="reference_lab.temperature_diagnostic",
            version="1",
            selection=selection,
        ),
    )
    diagnostic_run = lab.run(temperature_diagnostic.build(), config=resolved)
    assert diagnostic_run.status == "completed"
    assert diagnostic_run.snapshot.config_source == resolved.config_source
    assert lab.config.registry() == registry
    diagnostic = client.measurement_preview(diagnostic_run.id)
    assert diagnostic.items == diagnostic_run.measurements().records

    coherent = coherent_ramsey.build()
    coherent_run = lab.run(coherent, config=resolved)
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
    typed_values = coherent_run.result(coherent.output).rows(
        lambda point: point.value(coherent.output.iq_mean)
    )
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
    assert typed_values == tuple(
        complex(value["real"], value["imag"]) for value in expected_values
    )
    assert any(value["imag"] != 0 for value in expected_values)
    assert len({(value["real"], value["imag"]) for value in expected_values}) > 1
    # Keep this source lazy: the previously inspected dataset has loaded records.
    table = cast(
        "_ArrowProjection",
        coherent_run.measurements().project({("iq_mean"): mean_id}).to_arrow(),  # pyright: ignore[reportUnknownMemberType]
    )
    assert table.column("iq_mean").to_pylist() == expected_values
    assert table.column("point_index").to_pylist() == [
        record.point_index for record in coherent_data.records
    ]
    assert table.column("logical_point_id").to_pylist() == [
        record.logical_point_id for record in coherent_data.records
    ]

    before = client.list_runs()
    with lab.review(
        temperature_diagnostic.build(), config=resolved, name="Temperature diagnostic"
    ) as review:
        inspection = review.session
    assert client.list_runs() == before

    invocation = parallel_raw_ramsey.build()
    source = lab.run(
        invocation, config=resolved, name="Reference lab acceptance source"
    )
    analysis = (
        source.analysis("Pulse-shape proposal")
        .result()
        .propose(
            "q1-drag-beta",
            sc.parameter_update(
                QubitParameters.drag_beta,
                sc.EntityRef(id="q1", kind="logical_qubit"),
                1.0,
            ),
            reason="Explicit trial pulse shape; not a fitted or accepted calibration",
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
    # Candidate execution is evidence, not an operator approval or publication.
    proposals = client.parameter_proposals(source.id)
    assert all(item.approval is None for item in proposals.items)
    schema = source.measurements().schema

    with lab.instruments.open(
        temperature_readout("mixing-chamber"), setup=lab.setup.get("initial").ref
    ):
        procedure = lab.procedures.start(
            temperature_diagnostic_procedure,
            TemperatureDiagnosticIntent(initial_config=config, setup=setup.ref),
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

    assert lab.config.registry() == registry
    assert lab.setup.get("initial") == setup
    assert lab.parameters.get(parameters.id) == parameters

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
    proposals = proposals.model_copy(
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
                    }
                )
                for item in proposals.items
            ),
        }
    )
    cancelled = cancelled.model_copy(
        update={"run_id": "acceptance-waiting", "finished_at": FIXTURE_TIME}
    )
    return {
        "launch_catalog": catalog.model_dump(mode="json"),
        "launch_preview": launch_preview.model_dump(mode="json"),
        "planned_settings": setting_preview.model_dump(
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
        "candidate_proposal": proposals.model_dump(mode="json"),
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


def acceptance_json_matches(expected: str, actual: str) -> bool:
    """Compare the golden fixture, allowing only IQ reduction roundoff in ratio.

    Native trigonometry/reduction differs in its final bits across platforms.
    Captured values and their within-platform identity/Arrow checks stay exact.
    All non-IQ fields, including identities, schemas and provenance, stay exact.
    """
    return _acceptance_value_matches(
        cast("JsonValue", json.loads(expected)),
        cast("JsonValue", json.loads(actual)),
        (),
    )


def _acceptance_value_matches(
    expected: JsonValue, actual: JsonValue, path: tuple[str | int, ...]
) -> bool:
    if type(expected) is not type(actual):
        return False
    if isinstance(expected, dict) and isinstance(actual, dict):
        return expected.keys() == actual.keys() and all(
            _acceptance_value_matches(value, actual[key], (*path, key))
            for key, value in expected.items()
        )
    if isinstance(expected, list) and isinstance(actual, list):
        return len(expected) == len(actual) and all(
            _acceptance_value_matches(left, right, (*path, index))
            for index, (left, right) in enumerate(zip(expected, actual, strict=True))
        )
    if (
        isinstance(expected, float)
        and isinstance(actual, float)
        and len(path) == 7
        and path[:2] == ("coherent_scalar", "items")
        and path[3:6] == ("observables", "iq_mean", "value")
        and path[6] in ("real", "imag")
    ):
        # Values have unit ratio; this is a fixture comparison tolerance only.
        return math.isclose(expected, actual, rel_tol=1e-12, abs_tol=1e-12)
    return expected == actual
