from __future__ import annotations

import math
from typing import Literal, assert_type

import pytest
import scopecat as sc
from pydantic import ValidationError
from scopecat.measurements.results import (
    MeasurementArray,
    MeasurementScalar,
)

from scopecat_quantum import authoring
from scopecat_quantum.measurement_computes import (
    BinaryIqDiscriminator,
    BinaryIqProbabilityProducts,
    IqCentroid,
    binary_iq_probabilities,
)


def _discriminator(
    *,
    tie_policy: Literal["state_0", "state_1"] = "state_0",
) -> BinaryIqDiscriminator:
    return BinaryIqDiscriminator(
        state_0_centroid=IqCentroid(real=-1.0, imag=0.0),
        state_1_centroid=IqCentroid(real=1.0, imag=0.0),
        tie_policy=tie_policy,
    )


def _iq_shots(*values: complex) -> MeasurementArray:
    return MeasurementArray.create(
        dtype="complex128",
        unit="ratio",
        values=values,
    )


def test_binary_iq_discriminator_requires_finite_distinct_centroids() -> None:
    with pytest.raises(ValidationError, match="must be distinct"):
        BinaryIqDiscriminator(
            state_0_centroid=IqCentroid(real=0.0, imag=0.0),
            state_1_centroid=IqCentroid(real=0.0, imag=0.0),
        )
    with pytest.raises(ValidationError, match="must be finite"):
        IqCentroid(real=math.inf, imag=0.0)


@pytest.mark.parametrize(
    ("tie_policy", "expected"),
    [
        ("state_0", (0.75, 0.25)),
        ("state_1", (0.5, 0.5)),
    ],
)
def test_binary_iq_compute_classifies_one_point(
    tie_policy: Literal["state_0", "state_1"],
    expected: tuple[float, float],
) -> None:
    @authoring.program(id="test.binary-iq.kernel")
    def acquire_iq(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="iq_shots")

    @sc.module(id="test.binary-iq.kernel")
    def discriminate(module: sc.ModuleContext) -> BinaryIqProbabilityProducts:
        call = acquire_iq("q0").with_shots(4)
        module.use(call)
        return assert_type(
            binary_iq_probabilities(
                module,
                call.results.iq_shots,
                discriminator=_discriminator(tie_policy=tie_policy),
                id="discriminate",
            ),
            BinaryIqProbabilityProducts,
        )

    invocation_products = assert_type(
        discriminate().result,
        BinaryIqProbabilityProducts,
    )
    assert_type(invocation_products.probability_0, sc.ProductRef[float])
    assert_type(invocation_products.probability_1, sc.ProductRef[float])
    assert invocation_products.probability_0.id == "kernel/discriminate/probability_0"
    assert invocation_products.probability_1.id == "kernel/discriminate/probability_1"
    experiment = sc.ExperimentContext()
    records = assert_type(
        experiment.alias(experiment.use(discriminate())),
        sc.RecordedProducts,
    )
    assert isinstance(records.probability_0, sc.RecordRef)
    assert isinstance(records.probability_1, sc.RecordRef)
    declarations = {
        product.qualified_id: product
        for product in discriminate.definition.body.products
    }
    for product_id in (
        "discriminate/probability_0",
        "discriminate/probability_1",
    ):
        product = declarations[product_id]
        assert product.dtype == "float64"
        assert product.unit == "ratio"
        assert product.axes == ()

    [compute] = discriminate.definition.body.measurement_computes
    assert compute.input_bindings[0][1].qualified_name == "kernel/iq_shots"
    assert [name for name, _value in compute.value_input_bindings] == ["discriminator"]
    assert compute.captures == ()
    assert tuple(
        (role, product_id.qualified_name)
        for role, product_id in compute.output_bindings
    ) == (
        ("probability_0", "discriminate/probability_0"),
        ("probability_1", "discriminate/probability_1"),
    )

    outputs = compute.kernel(
        {
            "iq_shots": _iq_shots(
                -1.0 + 0.0j,
                -0.8 + 0.0j,
                1.0 + 0.0j,
                0.0 + 0.0j,
            ),
            "discriminator": _discriminator(tie_policy=tie_policy),
        }
    )
    assert outputs == {
        "probability_0": MeasurementScalar.create(
            dtype="float64",
            value=expected[0],
            unit="ratio",
        ),
        "probability_1": MeasurementScalar.create(
            dtype="float64",
            value=expected[1],
            unit="ratio",
        ),
    }


def test_binary_iq_compute_rejects_non_iq_input() -> None:
    @authoring.program(id="test.binary-iq.invalid-input")
    def acquire_iq(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="iq_shots")

    @sc.module(id="test.binary-iq.invalid-input")
    def discriminate(module: sc.ModuleContext) -> None:
        call = acquire_iq("q0").with_shots(1)
        module.use(call)
        binary_iq_probabilities(
            module,
            call.results.iq_shots,
            discriminator=_discriminator(),
        )

    [compute] = discriminate.definition.body.measurement_computes

    with pytest.raises(ValueError, match="1D array"):
        compute.kernel(
            {
                "iq_shots": MeasurementScalar.create(
                    dtype="float64",
                    value=0.0,
                    unit="ratio",
                ),
                "discriminator": _discriminator(),
            }
        )


def test_binary_iq_compute_uses_compute_identity_for_parallel_outputs() -> None:
    @authoring.program(id="test.binary-iq.namespaced")
    def acquire_iq(qubit: authoring.Qubit) -> authoring.QuantumFragment:
        return authoring.measure(qubit, result="iq_shots")

    @sc.module(id="test.binary-iq.namespaced")
    def discriminate(module: sc.ModuleContext) -> BinaryIqProbabilityProducts:
        call = acquire_iq("q0").with_shots(2)
        module.use(call)
        return binary_iq_probabilities(
            module,
            call.results.iq_shots,
            discriminator=_discriminator(),
            id="q0",
        )

    products = discriminate().result

    assert products.probability_0.id == "namespaced/q0/probability_0"
    assert products.probability_1.id == "namespaced/q0/probability_1"
