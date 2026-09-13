# pyright: reportUnusedFunction=false, reportCallInDefaultInitializer=false

"""Signature-owned controls keep one scalar/axis source through construction."""

from typing import Annotated

import pytest
from scopecat_testkit.authoring import load_config

import scopecat as sc
from scopecat.application.authoring import AuthorExperiment
from scopecat.application.controls import edit_controls
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.program.scans import ValuesScanSource
from scopecat.records.control_edit import ControlEdit
from scopecat.records.run_request import AxisValuesSourceRecord


@sc.experiment
def probe(
    ctx: sc.ExperimentContext,
    frequency: Annotated[
        sc.Input[sc.Quantity], sc.ControlSpec(minimum=4, maximum=6, scannable=True)
    ] = sc.Quantity(5, "GHz"),
    gain: Annotated[sc.Input[float], sc.ControlSpec(minimum=0, maximum=2)] = 1.0,
) -> tuple[sc.Input[sc.Quantity], sc.Input[float]]:
    return frequency, gain


def test_signature_defaults_and_coordinate_bind_have_one_source() -> None:
    declaration = AuthorExperiment.from_declaration(probe)
    assert declaration.entry.controls[0].default == sc.Quantity(5, "GHz")
    assert declaration.entry.request.properties == {}
    original = probe()
    changed = probe(frequency=sc.Quantity(5200, "MHz"), gain=1.5)
    request = compile_invocation(changed).request
    assert set(request.inputs) == {"gain"}
    assert request.inputs["gain"] == 1.5
    assert changed.definition is original.definition
    changed_source = changed.point_plan.domain.axes[0].source
    original_source = original.point_plan.domain.axes[0].source
    assert isinstance(changed_source, ValuesScanSource)
    assert isinstance(original_source, ValuesScanSource)
    assert changed_source.values == (sc.Quantity(5.2, "GHz"),)
    assert original_source.values == (sc.Quantity(5, "GHz"),)
    assert isinstance(changed.output[0], sc.CoordinateRef)
    assert probe.request().values == {"frequency": sc.Quantity(5, "GHz"), "gain": 1.0}
    with pytest.raises(ValueError, match="at most"):
        probe(frequency=sc.Quantity(7, "GHz"))


def test_required_control_is_discoverable_and_can_be_supplied_as_a_scan() -> None:
    @sc.experiment
    def required(
        ctx: sc.ExperimentContext,
        amplitude: Annotated[sc.Input[float], sc.ControlSpec(scannable=True)],
        gain: Annotated[sc.Input[float], sc.ControlSpec()],
    ) -> tuple[sc.Input[float], sc.Input[float]]:
        return amplitude, gain

    author = AuthorExperiment.from_declaration(required)
    assert all(control.default is None for control in author.entry.controls)
    with pytest.raises(ValueError, match="amplitude"):
        author.edit(config=load_config())
    built = author.edit(
        config=load_config(),
        edits={
            "amplitude": ControlEdit(
                mode="scan", axis=AxisValuesSourceRecord(values=[0.1, 0.2])
            ),
            "gain": ControlEdit(mode="fixed", value=1.0),
        },
    )
    assert compile_invocation(built).request.inputs == {"gain": 1.0}
    assert built.point_plan.domain.axes[0].mode == "scan"
    with pytest.raises(ValueError, match="gain: required"):
        edit_controls(
            author.controls,
            built,
            config=load_config(),
            edits={"gain": ControlEdit(mode="default")},
        )


def test_conflicting_metadata_and_duplicate_control_ownership_are_rejected() -> None:
    with pytest.raises(TypeError, match="metadata once"):

        @sc.experiment
        def duplicate(
            ctx: sc.ExperimentContext,
            value: Annotated[sc.Input[float], sc.FloatType(), sc.ControlSpec()] = 1,
        ) -> None:
            pass

    with pytest.raises(ValueError, match="control ids must be unique"):

        @sc.experiment(controls=sc.ControlSet((sc.Control("value", default=2),)))
        def repeated(
            ctx: sc.ExperimentContext,
            value: Annotated[sc.Input[float], sc.ControlSpec()] = 1,
        ) -> None:
            pass

    with pytest.raises(TypeError, match="requires an Input"):

        @sc.experiment
        def structural(
            ctx: sc.ExperimentContext,
            value: Annotated[float, sc.ControlSpec()] = 1,
        ) -> None:
            pass
