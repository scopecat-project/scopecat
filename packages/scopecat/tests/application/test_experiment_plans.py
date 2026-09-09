"""Plan projections preserve frozen inputs without retaining execution authority."""

from datetime import UTC, datetime
from typing import cast

import pytest

from scopecat.application.experiment_plans import (
    plan_launch_request,
    validate_plan_launch,
)
from scopecat.application.launch import LaunchCatalogEntry, LaunchInputSchema
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.control_edit import ControlEdit
from scopecat.records.experiment_plan import (
    ExperimentPlanDefinition,
    ExperimentPlanRevision,
)
from scopecat.records.plan_ref import ExperimentPlanRef, PlanConfigRef
from scopecat.records.run_request import RunRequest


def test_plan_frozen_inputs_current_actor_and_changed_definition() -> None:
    entry = LaunchCatalogEntry(
        id="signal",
        version="1",
        title="Signal",
        description="",
        actions=("preview", "submit"),
        kind="diagnostic",
        configuration_effect="none",
        request=LaunchInputSchema(),
    )
    definition = ExperimentPlanDefinition(
        experiment=entry.id,
        version=entry.version,
        definition_hash=sha256_json_hash(entry.model_dump(mode="json")),
        configuration=PlanConfigRef(
            entry_id="old-default", content_hash="sha256:" + "b" * 64
        ),
        inputs={"selected": [1, 3]},
        control_edits={"amplitude": ControlEdit(mode="fixed", value=0.1)},
    )
    with pytest.raises(TypeError, match="immutable"):
        cast("dict[str, object]", definition.inputs)["selected"] = [2]
    with pytest.raises(TypeError, match="immutable"):
        cast("dict[str, object]", definition.control_edits)["amplitude"] = None
    plan = ExperimentPlanRevision(
        ref=ExperimentPlanRef(
            plan_id="plan-1", revision=1, content_hash="sha256:" + "c" * 64
        ),
        name="Signal",
        definition=definition,
        saved_by="alice",
        saved_at=datetime.now(UTC),
    )
    request = plan_launch_request(plan, actor="bob")
    assert request.actor == "bob"
    assert request.request_key == ""
    assert request.expected_request_hash is None
    assert request.config_source is None
    assert request.configuration == definition.configuration
    assert request.inputs == {"selected": [1, 3]}
    validate_plan_launch(plan, request, entry)
    with pytest.raises(ValueError, match="differs"):
        validate_plan_launch(
            plan, request.model_copy(update={"inputs": {"selected": [2]}}), entry
        )
    with pytest.raises(ValueError, match="definition changed"):
        validate_plan_launch(
            plan, request, entry.model_copy(update={"description": "changed"})
        )


def test_legacy_run_request_identity_omits_absent_plan() -> None:
    original = RunRequest(experiment_id="legacy").model_dump(mode="json")
    assert "plan_ref" not in original
    encoded = RunRequest.model_validate(original).model_dump_json()
    assert RunRequest.model_validate_json(encoded).model_dump(mode="json") == original


def test_saved_entry_generation_is_not_a_calibration_activation() -> None:
    from scopecat.automation.calibrations import CalibrationConfigSourceRef
    from scopecat.records.run import ConfigRegistryRunConfigSource

    source = ConfigRegistryRunConfigSource(
        selector="saved",
        entry_id="saved",
        config_ref="saved",
        content_hash="sha256:" + "b" * 64,
        registry_generation=3,
    )
    with pytest.raises(ValueError, match="must select active"):
        CalibrationConfigSourceRef.from_run_config_source(source)


def test_run_request_view_plan_projection_roundtrip() -> None:
    from scopecat.daemon.views import RunRequestView

    ref = ExperimentPlanRef(
        plan_id="plan-1", revision=2, content_hash="sha256:" + "d" * 64
    )
    for selected in (None, ref):
        request = RunRequest(plan_ref=selected)
        view = RunRequestView(run_id="retained", request=request, plan_ref=selected)
        assert RunRequestView.model_validate_json(view.model_dump_json()) == view
    with pytest.raises(ValueError, match="projection is inconsistent"):
        RunRequestView(run_id="retained", request=RunRequest(), plan_ref=ref)
