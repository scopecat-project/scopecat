# pyright: reportUnnecessaryTypeIgnoreComment=true, reportCallInDefaultInitializer=false
# pyright: reportUnknownVariableType=false, reportUnknownArgumentType=false

"""Finite evidence for #1305; no new author interface contract."""

from typing import TYPE_CHECKING, Annotated, assert_type

import scopecat as sc
from scopecat.application.authoring import AuthorExperiment
from scopecat.authoring.request_inputs import capture_request_inputs
from scopecat.program.scans import ValuesScanSource


@sc.experiment
def frequency_probe(
    ctx: sc.ExperimentContext,
    frequency: Annotated[
        sc.Input[sc.Quantity], sc.ControlSpec(scannable=True)
    ] = sc.Quantity(5, "MHz"),
) -> None:
    pass


def test_typed_call_build_and_prepare_capture_have_distinct_boundaries() -> None:
    quantity = sc.Quantity(5000, "kHz")
    request = frequency_probe(quantity)
    assert request.values["frequency"] == quantity
    # The current ParamSpec rejects a number although the runtime control accepts it.
    bare = frequency_probe(5)  # pyright: ignore[reportArgumentType]
    assert bare.values["frequency"] == 5
    quantity_build = frequency_probe.build(quantity)
    bare_build = frequency_probe.build(5)  # pyright: ignore[reportArgumentType]
    for built in (quantity_build, bare_build):
        source = built.point_plan.domain.axes[0].source
        assert isinstance(source, ValuesScanSource)
        assert source.values == (sc.Quantity(5, "MHz"),)

    declaration = AuthorExperiment.from_declaration(frequency_probe)
    for edited in (5, quantity):
        request.values["frequency"] = edited
        # Exact helper called by AuthorProject.prepare, not an admission/daemon test.
        inputs, edits = capture_request_inputs(request, declaration.input_model)
        assert inputs == {}
        assert edits["frequency"].value == sc.Quantity(5, "MHz")
        assert request.values["frequency"] == edited


def test_eager_keeps_native_quantity_without_consumer_normalization() -> None:
    @sc.compute
    def magnitude(x: Annotated[sc.Quantity, sc.Unit("MHz")]) -> float:
        return x.value

    if TYPE_CHECKING:
        magnitude.eager(5)  # pyright: ignore[reportArgumentType]
    assert magnitude.eager(sc.Quantity(5000, "kHz")) == 5000
    assert magnitude.eager(sc.Quantity(5, "MHz")) == 5


def test_inferred_quantity_graph_works_but_static_decorator_rejects_it() -> None:
    @sc.module
    def explicit(ctx: sc.ModuleContext) -> sc.ValueRef[object]:
        return assert_type(
            ctx.compute(
                fn=lambda: sc.Quantity(0.125, "V"),
                output_type=sc.ScalarType(sc.QuantityType(unit="V")),
            ),
            sc.ValueRef[object],
        )

    assert explicit.definition.body.operations[0].output_type == sc.ScalarType(
        sc.QuantityType(unit="V")
    )

    # Quantity is not a ProductNativeValue: this is evidence, not a recommended API.
    @sc.compute  # pyright: ignore[reportArgumentType]
    def voltage() -> Annotated[sc.Quantity, sc.Unit("V")]:
        return sc.Quantity(0.125, "V")

    @sc.module
    def converted(ctx: sc.ModuleContext) -> sc.ValueRef[sc.Quantity]:
        value = voltage()
        assert isinstance(value, sc.ValueRef)
        return ctx.convert(value, "mV")

    producer, conversion = converted.definition.body.operations
    assert producer.output_type == sc.ScalarType(sc.QuantityType(unit="V"))
    assert conversion.output_type == sc.ScalarType(sc.QuantityType(unit="mV"))
    assert conversion.id == "convert_unit_value"
