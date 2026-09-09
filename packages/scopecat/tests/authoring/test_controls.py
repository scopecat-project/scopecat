# pyright: reportUnusedFunction=false

"""One declared control source survives notebook edits and planning admission."""

from __future__ import annotations

import json
from typing import Annotated

import pytest
from scopecat_testkit.authoring import load_config

import scopecat as sc
from scopecat.application.controls import ControlEdit, control_values, edit_controls
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.kernel.content_identity import content_fingerprint
from scopecat.kernel.errors import CheckFailed
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.service import plan_experiment_invocation
from scopecat.planning.system import ExperimentSystem
from scopecat.program.scans import ValuesScanSource
from scopecat.records.config import config_content_hash
from scopecat.records.run_request import AxisRecord

FREQUENCY = sc.Control(
    "frequency", default=sc.Quantity(5, "GHz"), minimum=4, maximum=6, scannable=True
)
AMPLITUDE = sc.Control(
    "amplitude", default=sc.Quantity(0.1, "V"), maximum=0.5, scannable=True
)


def _project_constraints(context: sc.ControlValidationContext) -> None:
    frequencies = FREQUENCY.axis_values(context.axis("frequency"))
    amplitudes = AMPLITUDE.axis_values(context.axis("amplitude"))
    frequency_max = max(
        value.value for value in frequencies if isinstance(value, sc.Quantity)
    )
    amplitude_max = max(
        value.value for value in amplitudes if isinstance(value, sc.Quantity)
    )
    if frequency_max > 5.5 and amplitude_max > 0.2:
        raise ValueError("high-frequency amplitude exceeds project limit")


CONTROLS = sc.ControlSet((FREQUENCY, AMPLITUDE), validator=_project_constraints)


@sc.experiment(id="controls-test", controls=CONTROLS)
def controlled(
    context: sc.ExperimentContext,
) -> tuple[sc.CoordinateRef[object], sc.CoordinateRef[object]]:
    return FREQUENCY.ref, AMPLITUDE.ref


def test_scalar_axis_replacement_has_one_source_and_preserves_explicit_scan() -> None:
    config = load_config()
    original = controlled()
    scalar = CONTROLS.apply(
        original, config=config, edits={"frequency": sc.Quantity(5200, "MHz")}
    )
    fixed = scalar.point_plan.domain.axes[0]
    assert fixed.mode == "fixed" and fixed.source == ValuesScanSource(
        (sc.Quantity(5.2, "GHz"),)
    )
    scanned = CONTROLS.apply(
        scalar,
        config=config,
        edits={"frequency": sc.axis(FREQUENCY.ref, [sc.Quantity(5.1, "GHz")])},
    )
    assert scanned.point_plan.domain.axes[0].mode == "scan"
    assert scanned.input_overrides == {}
    reset = CONTROLS.apply(scanned, config=config, reset=("frequency",))
    assert reset.point_plan.domain.axes[0] == original.point_plan.domain.axes[0]
    assert scalar.point_plan.domain.axes[0] == fixed
    assert '"mode":"fixed"' in compile_invocation(scalar).request.model_dump_json()


def test_project_validation_cannot_be_bypassed_by_direct_axis_edit() -> None:
    config = load_config()
    unsafe = (
        controlled()
        .with_axis(sc.axis(FREQUENCY.ref, [sc.Quantity(5.8, "GHz")]))
        .with_axis(sc.axis(AMPLITUDE.ref, [sc.Quantity(0.3, "V")]))
    )
    system = ExperimentSystem(
        InstrumentContractCatalog(config_content_hash=config_content_hash(config))
    )
    with pytest.raises(ValueError, match="project limit"):
        plan_experiment_invocation(unsafe, config=config, system=system)
    with pytest.raises(ValueError, match="project limit"):
        CONTROLS.apply(unsafe, config=config)
    with pytest.raises(ValueError, match="unknown control"):
        CONTROLS.apply(controlled(), config=config, edits={"missing": 1.0})
    with pytest.raises(ValueError, match="needs a scalar/default"):
        CONTROLS.apply(controlled().without_axis(FREQUENCY.ref), config=config)


def test_control_scalar_input_type_cannot_disagree_with_declaration() -> None:
    controls = sc.ControlSet((sc.Control("value", default=sc.Quantity(1, "V")),))
    with pytest.raises(TypeError, match="type must match"):

        @sc.experiment(controls=controls)
        def wrong(
            context: sc.ExperimentContext, value: sc.Input[float]
        ) -> sc.Input[float]:
            return value


def test_historical_scan_axis_json_and_identity_are_unchanged() -> None:
    historical = (
        '{"axis_id":"frequency","source":{"kind":"values","values":'
        '[{"value":5.0,"unit":"GHz"}]},"overlay":null}'
    )
    restored = AxisRecord.model_validate_json(historical)
    assert restored.mode == "scan"
    assert restored.model_dump_json() == historical
    assert content_fingerprint(restored.model_dump(mode="json")) == content_fingerprint(
        json.loads(historical)
    )


def test_scalar_bind_admission_and_owned_preview_normalization() -> None:
    def configured_value(context: sc.ControlValidationContext) -> sc.Quantity:
        return sc.Quantity(1000, "mV")

    controls = sc.ControlSet(
        (
            sc.Control("value", default=1.0, maximum=2.0),
            sc.Control(
                "configured",
                unit="V",
                ownership="configuration",
                resolve=configured_value,
                provenance="Frozen project configuration",
            ),
        )
    )

    @sc.experiment(controls=controls)
    def scalar(
        context: sc.ExperimentContext, value: sc.Input[float]
    ) -> sc.Input[float]:
        return value

    config = load_config()
    invocation = scalar.bind()
    assert compile_invocation(invocation).request.inputs == {"value": 1.0}
    values = control_values(controls, invocation, config=config)
    assert values[1].value == sc.Quantity(1, "V")
    assert values[1].provenance == "Frozen project configuration"
    with pytest.raises(ValueError, match="configuration-owned"):
        edit_controls(
            controls,
            invocation,
            config=config,
            edits={"configured": ControlEdit(mode="fixed", value=sc.Quantity(2, "V"))},
        )
    system = ExperimentSystem(
        InstrumentContractCatalog(config_content_hash=config_content_hash(config))
    )
    with pytest.raises(CheckFailed, match="at most"):
        plan_experiment_invocation(
            invocation.bind(value=3.0), config=config, system=system
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "scan", "value": 1.0, "axis": {"kind": "values", "values": [2.0]}},
        {"mode": "default", "value": 1.0},
        {"mode": "fixed", "value": 1.0, "axis": {"kind": "values", "values": [2.0]}},
    ],
)
def test_wire_edit_cannot_carry_an_inactive_source(payload: dict[str, object]) -> None:
    with pytest.raises(ValueError, match="control edit"):
        ControlEdit.model_validate(payload)


@pytest.mark.parametrize("resolved", [sc.Quantity(3, "V"), sc.Quantity(1, "GHz")])
def test_owned_preview_rejects_invalid_resolved_value(resolved: sc.Quantity) -> None:
    controls = sc.ControlSet(
        (
            sc.Control(
                "owned",
                unit="V",
                maximum=2,
                ownership="derived",
                resolve=lambda _: resolved,
            ),
        )
    )

    @sc.experiment(controls=controls)
    def owned(context: sc.ExperimentContext) -> None:
        return None

    with pytest.raises((ValueError, TypeError)):
        control_values(controls, owned(), config=load_config())


def test_control_range_must_fit_its_declared_input_type() -> None:
    controls = sc.ControlSet((sc.Control("value", default=1.5, minimum=0, maximum=2),))
    with pytest.raises(TypeError, match="type must match"):

        @sc.experiment(controls=controls)
        def narrower(
            context: sc.ExperimentContext,
            value: Annotated[sc.Input[float], sc.FloatType(minimum=1)],
        ) -> sc.Input[float]:
            return value
