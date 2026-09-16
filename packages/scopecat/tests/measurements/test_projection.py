from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pytest
from scopecat_testkit.measurement_assembly import (
    assembled_measurement_values_for_all_uses,
    measurement_assembly_scenario,
    measurement_value_candidates,
)

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_types import (
    Array,
    ArrayDimension,
    Complex,
    Float,
    Scalar,
    TableColumn,
)
from scopecat.kernel.value_types import (
    Quantity as QuantityType,
)
from scopecat.measurements.dataset import Dataset
from scopecat.measurements.products import ProductAxisDef
from scopecat.measurements.projection import (
    MeasurementProjection,
    project_measurement_records,
    select_measurement_projection,
)
from scopecat.measurements.recording_arrow import (
    decode_measurement_append,
    encode_measurement_append,
)
from scopecat.measurements.records import (
    EntityRecordUse,
    EntityRecordUseMember,
    ValueRecordCandidate,
    ValueRecordUse,
    expected_dataset_schema,
)
from scopecat.measurements.results import (
    EntityAcquisitionEvidence,
    InstrumentAcquisitionEvidence,
    MeasurementPointDomainAxis,
    MeasurementPointDomainLinearSource,
    MeasurementPointDomainRangeSource,
    MeasurementPointDomainValuesSource,
    MeasurementProductGridPointDomain,
    MeasurementScalar,
)
from scopecat.measurements.values import (
    seal_measurement_values,
)
from scopecat.planning.measurement_projection import (
    project_run_point_catalog,
)
from scopecat.program.measurement_types import EntityAcquisitionSemantics
from scopecat.program.point_domain import (
    point_axis_linear,
    point_axis_range,
    point_axis_values,
)
from scopecat.program.products import EntityAxisDef
from scopecat.records.content import ContentEntry
from scopecat.records.measurement import (
    MeasurementArray,
    MeasurementDataset,
    MeasurementSegmentedArray,
    MeasurementUnavailable,
    measurement_point_axis_values,
)
from scopecat.records.measurement_recording import MeasurementDatasetAppend


def test_projection_keeps_all_records_without_narrowing_the_value_catalog() -> None:
    scenario = measurement_assembly_scenario(use_count=3)
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    assert projection.catalog.product_use_ids == tuple(use.id for use in scenario.uses)
    assert tuple(record.id for record in projection.records) == (
        "primary",
        "alias",
        "secondary",
    )


def test_projection_records_symbolic_scalar_values_without_product_provenance() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0, 1.0), use_count=0)
    value_id = ValueId(SymbolId(local_id="score"))
    value_record = ValueRecordUse(
        id="score",
        value_id=value_id,
        source_value_id="analysis/score",
        value_type=Scalar(Float()),
        requires_execution=True,
    )
    candidates = tuple(
        ValueRecordCandidate(
            logical_point_id=point.logical_id,
            value_id=value_id,
            value=float(point.ordinal + 1),
        )
        for point in scenario.points
    )
    projection = select_measurement_projection(
        scenario.catalog,
        (value_record,),
    )
    values = seal_measurement_values(scenario.catalog, (), points=scenario.points)

    projected = project_measurement_records(
        projection,
        values,
        run_id="value-record-run",
        points=scenario.points,
        value_candidates=candidates,
    )

    assert [record.observables["score"] for record in projected.records] == [
        MeasurementScalar.create(dtype="float64", value=1.0),
        MeasurementScalar.create(dtype="float64", value=2.0),
    ]
    schema = projection.schema
    assert schema is not None
    variable = next(item for item in schema.variables if item.id == "score")
    assert variable.source_product_id is None
    assert variable.source_value_id == "analysis/score"


@pytest.mark.parametrize("value", [1.25 - 2.5j, np.complex128(1.25 - 2.5j), 2.0])
def test_projection_preserves_declared_complex_scalar_and_unit(
    value: complex | np.complex128,
) -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=0)
    value_id = ValueId(SymbolId(local_id="mean-iq"))
    projection = select_measurement_projection(
        scenario.catalog,
        (
            ValueRecordUse(
                id="iq",
                value_id=value_id,
                source_value_id="analysis/mean-iq",
                value_type=Scalar(Complex(unit="V")),
                requires_execution=True,
            ),
        ),
    )
    values = seal_measurement_values(scenario.catalog, (), points=scenario.points)
    projected = project_measurement_records(
        projection,
        values,
        run_id="complex-value-record-run",
        points=scenario.points,
        value_candidates=(
            ValueRecordCandidate(
                logical_point_id=scenario.points[0].logical_id,
                value_id=value_id,
                value=value,
            ),
        ),
    )
    actual = projected.records[0].observables["iq"]
    expected = MeasurementScalar.create(dtype="complex128", unit="V", value=value)
    assert actual == expected
    assert MeasurementScalar.model_validate_json(expected.model_dump_json()) == expected


def test_projection_records_symbolic_array_values_with_local_dimensions() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=0)
    value_id = ValueId(SymbolId(local_id="trace"))
    value_record = ValueRecordUse(
        id="trace",
        value_id=value_id,
        source_value_id="analysis/trace",
        value_type=Array(
            dtype="float64",
            dimensions=(ArrayDimension("sample", 3, kind="time", unit="ns"),),
            unit="V",
        ),
        requires_execution=True,
    )
    [point] = scenario.points
    projection = select_measurement_projection(
        scenario.catalog,
        (value_record,),
    )
    values = seal_measurement_values(scenario.catalog, (), points=scenario.points)

    projected = project_measurement_records(
        projection,
        values,
        run_id="array-value-record-run",
        points=scenario.points,
        value_candidates=(
            ValueRecordCandidate(
                logical_point_id=point.logical_id,
                value_id=value_id,
                value=np.asarray([1.0, 2.0, 3.0]),
            ),
        ),
    )

    trace = projected.records[0].observables["trace"]
    assert isinstance(trace, MeasurementArray)
    assert trace.unit == "V"
    assert trace.values.tolist() == [1.0, 2.0, 3.0]
    schema = projection.schema
    assert schema is not None
    dimensions = [
        (dimension.id, dimension.kind, dimension.size)
        for dimension in schema.dimensions
    ]
    assert dimensions == [
        ("point", "point", 1),
        ("sample", "time", 3),
    ]
    variable = next(item for item in schema.variables if item.id == "trace")
    assert variable.dims == ("point", "sample")


def test_projection_promotes_product_entity_values_to_a_labeled_dimension() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=1)
    entities = (
        EntityRef(id="q0", kind="logical_qubit"),
        EntityRef(id="q1", kind="logical_qubit"),
    )
    [product] = scenario.catalog.product_defs
    product = replace(
        product,
        axes=(
            ProductAxisDef(
                id="qubit",
                dimension_id="qubit",
                dimension_label="qubit",
                kind="entity",
                size=2,
                entities=entities,
            ),
        ),
    )
    catalog = replace(scenario.catalog, product_defs=(product,))

    projection = select_measurement_projection(catalog, scenario.records)

    schema = projection.schema
    assert schema is not None
    qubit = next(
        dimension for dimension in schema.dimensions if dimension.id == "qubit"
    )
    assert qubit.index is not None
    assert qubit.index.entity_kind == "logical_qubit"
    assert qubit.index.values == entities


def test_projection_stacks_entity_products_and_preserves_partial_failure() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0, 1.0), use_count=2)
    q0 = EntityRef(id="q0", kind="qubit")
    q1 = EntityRef(id="q1", kind="qubit")
    records = (
        EntityRecordUse(
            id="readout",
            axis=EntityAxisDef(
                id="qubit",
                values=(q0, q1),
            ),
            members=(
                EntityRecordUseMember(
                    entity=q0,
                    product_use_id=scenario.uses[0].id,
                ),
                EntityRecordUseMember(
                    entity=q1,
                    product_use_id=scenario.uses[1].id,
                ),
            ),
        ),
    )
    projection = select_measurement_projection(scenario.catalog, records)
    candidates = list(measurement_value_candidates(scenario, scenario.uses))
    q0_evidence = InstrumentAcquisitionEvidence(
        command_id="readout-q0",
        instrument_id="readout",
        interface_id="test.scalar_signal/v1",
        acquisition_id="sample",
        result_id="q0",
        started_at=datetime(2026, 8, 15, 10, 0, tzinfo=UTC),
        completed_at=datetime(2026, 8, 15, 10, 0, 1, tzinfo=UTC),
    )
    q1_evidence = q0_evidence.model_copy(
        update={"command_id": "readout-q1", "result_id": "q1"}
    )
    candidates[0] = replace(candidates[0], evidence=q0_evidence)
    candidates[1] = replace(
        candidates[1],
        value=MeasurementUnavailable.create(
            reason="missing",
            dtype="float64",
            unit="ratio",
            shape=(),
            metadata={"source": "q1"},
        ),
        evidence=q1_evidence,
    )
    assembled = seal_measurement_values(
        scenario.catalog,
        candidates,
        points=scenario.points,
    )

    strict_projection = select_measurement_projection(
        scenario.catalog,
        (
            replace(
                records[0],
                acquisition=EntityAcquisitionSemantics(
                    policy="all_or_nothing",
                    cohort_id="strict-readout",
                ),
            ),
        ),
    )
    [strict_cohort] = strict_projection.acquisition_cohorts
    assert strict_cohort.id == "strict-readout"
    assert strict_cohort.dimension_id == "qubit"
    assert strict_cohort.entities == (q0, q1)
    assert strict_cohort.members[0].product_use_ids == tuple(
        use.id for use in scenario.uses
    )
    with pytest.raises(ValueError, match="violates all_or_nothing"):
        project_measurement_records(
            strict_projection,
            assembled,
            run_id="strict-entity-projection-run",
            points=scenario.points,
        )

    best_effort_projection = select_measurement_projection(
        scenario.catalog,
        (
            replace(
                records[0],
                acquisition=EntityAcquisitionSemantics(
                    policy="best_effort",
                    cohort_id="readout-command",
                ),
            ),
        ),
    )
    with pytest.raises(ValueError, match="spans multiple hardware commands"):
        project_measurement_records(
            best_effort_projection,
            assembled,
            run_id="split-command-entity-projection-run",
            points=scenario.points,
        )

    projected = project_measurement_records(
        projection,
        assembled,
        run_id="entity-projection-run",
        points=scenario.points,
    )

    assert len(projection.records) == 1
    schema = projection.schema
    assert schema is not None
    readout = next(
        variable for variable in schema.variables if variable.id == "readout"
    )
    assert readout.dims == ("point", "qubit")
    assert readout.source_entity_products is not None
    assert readout.entity_acquisition is not None
    assert readout.entity_acquisition.policy == "independent"
    assert readout.source_entity_products.product_ids == ("signal-0", "signal-1")
    assert readout.source_entity_products.metadata_for(0) == {"definition": 0}
    assert readout.source_entity_products.metadata_for(1) == {"definition": 1}
    first = projected.records[0].observables["readout"]
    assert isinstance(first, MeasurementArray)
    assert first.values.tolist() == [0.0, 0.0]
    assert first.availability is not None
    assert first.availability.valid.tolist() == [True, False]
    [failure] = first.availability.unavailable
    assert failure.reason == "missing"
    assert failure.metadata == {"source": "q1"}
    second = projected.records[1].observables["readout"]
    assert isinstance(second, MeasurementArray)
    assert second.availability is None
    assert second.values.tolist() == [100.0, 101.0]
    assert projected.records[0].acquisition_evidence.for_variable("readout") == (
        EntityAcquisitionEvidence(
            dimension_id="qubit",
            values=(q0_evidence, q1_evidence),
        )
    )


def test_entity_acquisition_cohort_factors_multiple_recorded_fields() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=4)
    entities = (
        EntityRef(id="q0", kind="qubit"),
        EntityRef(id="q1", kind="qubit"),
    )
    axis = EntityAxisDef(id="qubit", values=entities)
    acquisition = EntityAcquisitionSemantics(
        policy="best_effort",
        cohort_id="readout",
    )
    projection = select_measurement_projection(
        scenario.catalog,
        tuple(
            EntityRecordUse(
                id=record_id,
                axis=axis,
                members=tuple(
                    EntityRecordUseMember(entity=entity, product_use_id=use.id)
                    for entity, use in zip(
                        entities,
                        scenario.uses[offset : offset + 2],
                        strict=True,
                    )
                ),
                acquisition=acquisition,
            )
            for record_id, offset in (("i", 0), ("q", 2))
        ),
    )

    [cohort] = projection.acquisition_cohorts
    assert cohort.id == "readout"
    assert tuple(member.record_id for member in cohort.members) == ("i", "q")
    assert tuple(member.product_use_ids for member in cohort.members) == (
        tuple(use.id for use in scenario.uses[:2]),
        tuple(use.id for use in scenario.uses[2:]),
    )


def test_entity_projection_uses_explicit_ragged_segments() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=2)
    product_defs = tuple(
        replace(
            product,
            axes=(
                ProductAxisDef(
                    id="sample",
                    dimension_id="sample",
                    dimension_label="sample",
                    kind="sample",
                    size=None,
                ),
            ),
        )
        for product in scenario.catalog.product_defs
    )
    catalog = replace(scenario.catalog, product_defs=product_defs)
    entities = (
        EntityRef(id="q0", kind="qubit"),
        EntityRef(id="q1", kind="qubit"),
    )
    projection = select_measurement_projection(
        catalog,
        (
            EntityRecordUse(
                id="trace",
                axis=EntityAxisDef(
                    id="qubit",
                    values=entities,
                ),
                members=tuple(
                    EntityRecordUseMember(entity=entity, product_use_id=use.id)
                    for entity, use in zip(entities, scenario.uses, strict=True)
                ),
            ),
        ),
    )
    candidates = tuple(
        replace(
            candidate,
            value=MeasurementArray.create(
                values=np.arange(size, dtype=np.float64),
                dtype="float64",
                unit="ratio",
            ),
        )
        for candidate, size in zip(
            measurement_value_candidates(scenario, scenario.uses),
            (2, 3),
            strict=True,
        )
    )
    assembled = seal_measurement_values(catalog, candidates, points=scenario.points)

    projected = project_measurement_records(
        projection,
        assembled,
        run_id="entity-ragged-run",
        points=scenario.points,
    )

    value = projected.records[0].observables["trace"]
    assert isinstance(value, MeasurementSegmentedArray)
    assert value.shape == (2, None)
    assert value.segment_shapes == ((2,), (3,))
    assert value.values.tolist() == [0.0, 1.0, 0.0, 1.0, 2.0]
    assert value.availability is None
    schema = projection.schema
    assert schema is not None
    restored = decode_measurement_append(
        encode_measurement_append(
            MeasurementDatasetAppend(
                run_id=projected.run_id,
                header_content_hash="sha256:header",
                acquisition_start=0,
                records=projected.records,
            ),
            schema,
        ),
        schema,
    )
    restored_value = restored.records[0].observables["trace"]
    assert isinstance(restored_value, MeasurementSegmentedArray)
    assert restored_value == value
    dataset = Dataset(
        MeasurementDataset(dataset_schema=schema, records=projected.records),
        ContentEntry(
            role="dataset",
            id="raw-measurements",
            kind="measurement_dataset",
            content_hash="entity-ragged",
            schema=schema.model_dump(mode="json"),
        ),
    )
    observations = dataset["trace"].observations
    assert observations.values.tolist() == value.values.tolist()
    assert observations.coords["trace__qubit__sample__qubit_index"].tolist() == [
        0,
        0,
        1,
        1,
        1,
    ]
    assert observations.coords["trace__qubit__sample__sample_index"].tolist() == [
        0,
        1,
        0,
        1,
        2,
    ]
    q1_dataset = dataset.isel(qubit=[1])
    q1_value = q1_dataset.records[0].observables["trace"]
    assert isinstance(q1_value, MeasurementSegmentedArray)
    assert q1_value.segment_shapes == ((3,),)
    assert q1_dataset["trace"].observations.values.tolist() == [0.0, 1.0, 2.0]
    expanded = dataset.reindex_entities(
        "qubit",
        (*entities, EntityRef(id="q2", kind="qubit")),
    )
    expanded_value = expanded.records[0].observables["trace"]
    assert isinstance(expanded_value, MeasurementSegmentedArray)
    assert expanded_value.segment_shapes == ((2,), (3,), (None,))
    assert isinstance(expanded_value.segments[2], MeasurementUnavailable)
    assert expanded["trace"].observations.values.tolist() == value.values.tolist()
    restored_expanded = decode_measurement_append(
        encode_measurement_append(
            MeasurementDatasetAppend(
                run_id=projected.run_id,
                header_content_hash="sha256:expanded",
                acquisition_start=0,
                records=expanded.records,
            ),
            expanded.schema,
        ),
        expanded.schema,
    )
    assert restored_expanded.records[0].observables["trace"] == expanded_value

    failed_candidates = (
        candidates[0],
        replace(
            candidates[1],
            value=MeasurementUnavailable.create(
                reason="missing",
                dtype="float64",
                unit="ratio",
                shape=(3,),
                metadata={"entity": "q1"},
            ),
        ),
    )
    failed = (
        project_measurement_records(
            projection,
            seal_measurement_values(
                catalog,
                failed_candidates,
                points=scenario.points,
            ),
            run_id="entity-ragged-partial-run",
            points=scenario.points,
        )
        .records[0]
        .observables["trace"]
    )
    assert isinstance(failed, MeasurementSegmentedArray)
    assert failed.segment_shapes == ((2,), (3,))
    assert failed.availability is not None
    assert failed.availability.valid.tolist() == [True, True, False, False, False]
    [unavailable] = failed.availability.unavailable
    assert unavailable.flat_indices == (2, 3, 4)
    assert unavailable.metadata == {"entity": "q1"}


def test_entity_projection_keeps_schema_width_constant_across_sources() -> None:
    entity_count = 3
    scenario = measurement_assembly_scenario(
        point_values=(0.0,),
        use_count=entity_count,
    )
    entities = tuple(
        EntityRef(id=f"q{index}", kind="qubit") for index in range(entity_count)
    )
    records = (
        EntityRecordUse(
            id="readout",
            axis=EntityAxisDef(
                id="qubit",
                values=entities,
            ),
            members=tuple(
                EntityRecordUseMember(entity=entity, product_use_id=use.id)
                for entity, use in zip(entities, scenario.uses, strict=True)
            ),
        ),
    )

    projection = select_measurement_projection(scenario.catalog, records)

    assert len(projection.records) == 1
    schema = projection.schema
    assert schema is not None
    assert [variable.id for variable in schema.variables] == ["x", "readout"]
    readout = schema.variables[1]
    assert readout.source_entity_products is not None
    assert len(readout.source_entity_products.product_ids) == entity_count
    qubit = next(
        dimension for dimension in schema.dimensions if dimension.id == "qubit"
    )
    assert qubit.index is not None
    assert len(qubit.index.values) == entity_count


def test_entity_projection_normalizes_common_product_metadata() -> None:
    scenario = measurement_assembly_scenario(use_count=2, shared_product=True)
    entities = (
        EntityRef(id="q0", kind="qubit"),
        EntityRef(id="q1", kind="qubit"),
    )
    projection = select_measurement_projection(
        scenario.catalog,
        (
            EntityRecordUse(
                id="readout",
                axis=EntityAxisDef(
                    id="qubit",
                    values=entities,
                ),
                members=tuple(
                    EntityRecordUseMember(entity=entity, product_use_id=use.id)
                    for entity, use in zip(entities, scenario.uses, strict=True)
                ),
            ),
        ),
    )

    assert projection.schema is not None
    readout = next(
        variable for variable in projection.schema.variables if variable.id == "readout"
    )
    assert readout.source_entity_products is not None
    assert readout.source_entity_products.common_metadata == {"definition": "shared"}
    assert readout.source_entity_products.metadata_overrides == ()


def test_entity_projection_compresses_one_common_failure_without_losing_axis() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=2)
    entities = (
        EntityRef(id="q0", kind="qubit"),
        EntityRef(id="q1", kind="qubit"),
    )
    records = (
        EntityRecordUse(
            id="readout",
            axis=EntityAxisDef(
                id="qubit",
                values=entities,
            ),
            members=tuple(
                EntityRecordUseMember(entity=entity, product_use_id=use.id)
                for entity, use in zip(entities, scenario.uses, strict=True)
            ),
        ),
    )
    projection = select_measurement_projection(scenario.catalog, records)
    candidates = tuple(
        replace(
            candidate,
            value=MeasurementUnavailable.create(
                reason="missing",
                dtype="float64",
                unit="ratio",
                shape=(),
                metadata={"cohort": "readout"},
            ),
        )
        for candidate in measurement_value_candidates(scenario, scenario.uses)
    )
    assembled = seal_measurement_values(
        scenario.catalog,
        candidates,
        points=scenario.points,
    )

    projected = project_measurement_records(
        projection,
        assembled,
        run_id="entity-common-failure",
        points=scenario.points,
    )

    value = projected.records[0].observables["readout"]
    assert isinstance(value, MeasurementArray)
    assert value.availability is not None
    assert value.availability.valid.tolist() == [False, False]
    [failure] = value.availability.unavailable
    assert failure.reason == "missing"
    assert failure.flat_indices == (0, 1)
    assert failure.metadata == {"cohort": "readout"}


def test_projection_schema_persists_ordered_product_grid_axes() -> None:
    scenario = measurement_assembly_scenario(
        point_values=(0.0, 1.0, 2.0),
        use_count=1,
    )
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    schema = projection.schema

    assert schema is not None
    assert schema.point_domain == MeasurementProductGridPointDomain(
        axes=[
            MeasurementPointDomainAxis(
                id="x",
                size=3,
                source=MeasurementPointDomainValuesSource(
                    values=[
                        MeasurementScalar.create(value=value)
                        for value in (0.0, 1.0, 2.0)
                    ]
                ),
            ),
            MeasurementPointDomainAxis(
                id="opaque",
                size=1,
                source=MeasurementPointDomainValuesSource(values=[None]),
            ),
        ]
    )
    assert schema.metadata == {"experiment_id": "test.bound-program"}


def test_product_grid_schema_infers_coordinate_unit_from_axis_values() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0, 1.0), use_count=1)
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    schema = expected_dataset_schema(
        experiment_id="quantity-axis",
        point_count=2,
        records=projection.records,
        point_coordinate_columns=(TableColumn("frequency", Scalar(QuantityType())),),
        point_domain_axes=(
            point_axis_values(
                "frequency",
                Scalar(QuantityType()),
                (
                    Quantity(value=4.9, unit="GHz"),
                    Quantity(value=5.1, unit="GHz"),
                ),
            ),
        ),
    )

    assert schema is not None
    frequency = next(
        variable for variable in schema.variables if variable.id == "frequency"
    )
    assert frequency.unit == "GHz"


def test_product_grid_schema_retains_large_range_as_compact_source() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=1)
    projection = select_measurement_projection(scenario.catalog, scenario.records)
    point_count = 1_000_000

    schema = expected_dataset_schema(
        experiment_id="compact-range-axis",
        point_count=point_count,
        records=projection.records,
        point_coordinate_columns=(TableColumn("index", Scalar(Float())),),
        point_domain_axes=(
            point_axis_range(
                "index",
                Scalar(Float()),
                0.0,
                float(point_count - 1),
                point_count,
            ),
        ),
    )

    assert schema is not None
    assert isinstance(schema.point_domain, MeasurementProductGridPointDomain)
    [axis] = schema.point_domain.axes
    assert axis.size == point_count
    assert axis.source == MeasurementPointDomainRangeSource(
        start=MeasurementScalar.create(value=0.0),
        stop=MeasurementScalar.create(value=float(point_count - 1)),
    )


def test_product_grid_schema_round_trips_centered_axis_generation() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=1)
    projection = select_measurement_projection(scenario.catalog, scenario.records)
    frequency_type = Scalar(QuantityType(unit="GHz"))

    schema = expected_dataset_schema(
        experiment_id="centered-axis",
        point_count=3,
        records=projection.records,
        point_coordinate_columns=(TableColumn("frequency", frequency_type),),
        point_domain_axes=(
            point_axis_linear(
                "frequency",
                frequency_type,
                Quantity(5.0, "GHz"),
                Quantity(2.0, "GHz"),
                3,
            ),
        ),
    )

    assert schema is not None
    restored = type(schema).model_validate(schema.model_dump(mode="json"))
    assert isinstance(restored.point_domain, MeasurementProductGridPointDomain)
    [axis] = restored.point_domain.axes
    assert axis.source == MeasurementPointDomainLinearSource(
        center=MeasurementScalar.create(value=5.0, unit="GHz"),
        span=MeasurementScalar.create(value=2.0, unit="GHz"),
    )
    assert measurement_point_axis_values(axis) == tuple(
        MeasurementScalar.create(value=value, unit="GHz") for value in (4.0, 5.0, 6.0)
    )


def test_product_grid_schema_rejects_inconsistent_coordinate_units() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0, 1.0), use_count=1)
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    with pytest.raises(ValueError, match="inconsistent quantity units"):
        expected_dataset_schema(
            experiment_id="quantity-axis",
            point_count=2,
            records=projection.records,
            point_coordinate_columns=(
                TableColumn("frequency", Scalar(QuantityType())),
            ),
            point_domain_axes=(
                point_axis_values(
                    "frequency",
                    Scalar(QuantityType()),
                    (
                        Quantity(value=4.9, unit="GHz"),
                        Quantity(value=5_100.0, unit="MHz"),
                    ),
                ),
            ),
        )


def test_projection_preserves_order_across_product_and_value_records() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=1)
    value_id = ValueId(SymbolId(local_id="score"))
    projection = select_measurement_projection(
        scenario.catalog,
        (
            ValueRecordUse(
                id="score",
                value_id=value_id,
                source_value_id="analysis/score",
                value_type=Scalar(Float()),
            ),
            *scenario.records,
        ),
    )

    assert [record.id for record in projection.records] == [
        "score",
        "primary",
        "alias",
    ]


def test_value_projection_contract_uses_stable_semantic_source_identity() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=0)

    def projection_for(transient_id: str) -> MeasurementProjection:
        return select_measurement_projection(
            scenario.catalog,
            (
                ValueRecordUse(
                    id="score",
                    value_id=ValueId(
                        SymbolId(scope=("values",), local_id=transient_id)
                    ),
                    source_value_id="analysis/score",
                    value_type=Scalar(Float()),
                ),
            ),
        )

    first = projection_for("first-runtime-value")
    second = projection_for("second-runtime-value")

    assert first.contract_fingerprint == second.contract_fingerprint
    assert first.schema == second.schema


def test_projection_schema_keeps_the_complete_planned_point_count() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0, 1.0, 2.0), use_count=2)
    projection = select_measurement_projection(
        scenario.catalog,
        scenario.records,
    )

    selected_points = project_run_point_catalog(scenario.bound_points, (1, 2)).points
    ordinals = tuple(point.ordinal for point in selected_points)
    assert ordinals == (1, 2)
    assert projection.coordinate_ids == ("x",)
    schema = projection.schema
    assert schema is not None
    assert (
        next(
            dimension for dimension in schema.dimensions if dimension.id == "point"
        ).size
        == 3
    )


def test_dimension_identity_changes_catalog_and_projection_contracts() -> None:
    scenario = measurement_assembly_scenario(use_count=2)
    product = scenario.catalog.product_defs[0]
    first_product = replace(
        product,
        axes=(
            ProductAxisDef(
                id="sample",
                dimension_id="product/first/sample",
                kind="sample",
                size=2,
            ),
        ),
    )
    second_product = replace(
        first_product,
        axes=(
            replace(
                first_product.axes[0],
                dimension_id="product/second/sample",
            ),
        ),
    )
    first_catalog = replace(
        scenario.catalog,
        product_defs=(first_product, *scenario.catalog.product_defs[1:]),
    )
    second_catalog = replace(
        scenario.catalog,
        product_defs=(second_product, *scenario.catalog.product_defs[1:]),
    )

    assert first_catalog.contract_fingerprint != second_catalog.contract_fingerprint
    assert (
        select_measurement_projection(
            first_catalog, scenario.records
        ).contract_fingerprint
        != select_measurement_projection(
            second_catalog, scenario.records
        ).contract_fingerprint
    )


def test_recording_group_is_part_of_the_projection_contract_and_schema() -> None:
    scenario = measurement_assembly_scenario(use_count=2)
    ungrouped = select_measurement_projection(scenario.catalog, scenario.records)
    grouped_records = tuple(
        replace(record, recording_group_id="readout/sweep")
        for record in scenario.records
    )
    grouped = select_measurement_projection(scenario.catalog, grouped_records)

    assert grouped.contract_fingerprint != ungrouped.contract_fingerprint
    schema = grouped.schema
    assert schema is not None
    assert [group.model_dump(mode="python") for group in schema.variable_groups] == [
        {
            "id": "readout/sweep",
            "metadata": {},
        }
    ]
    assert {variable.recording_group_id for variable in schema.variables} == {
        None,
        "readout/sweep",
    }
    assert {
        variable.recording_group_id
        for variable in schema.variables
        if variable.id in {record.id for record in grouped_records}
    } == {"readout/sweep"}


def test_record_aliases_project_one_value_twice_without_expanding_assembly() -> None:
    scenario, assembled = assembled_measurement_values_for_all_uses()
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    projected = project_measurement_records(
        projection,
        assembled,
        run_id="projection-run",
        points=scenario.points,
    )

    assert len(assembled.values) == len(scenario.bound_points.point_domain.points) * 3
    assert len(projected.records) == 2
    assert [record.point_index for record in projected.records] == [0, 1]
    for record in projected.records:
        assert set(record.observables) == {"primary", "alias", "secondary"}
        assert record.observables["primary"] == record.observables["alias"]


def test_record_coordinates_project_as_inner_coordinate_variables() -> None:
    scenario, assembled = assembled_measurement_values_for_all_uses()
    observable_projection = select_measurement_projection(
        scenario.catalog,
        scenario.records,
    )
    records = (
        replace(scenario.records[0], role="coordinate"),
        *scenario.records[1:],
    )
    projection = select_measurement_projection(scenario.catalog, records)

    assert projection.contract_fingerprint != observable_projection.contract_fingerprint
    projected = project_measurement_records(
        projection,
        assembled,
        run_id="coordinate-product-run",
        points=scenario.points,
    )

    schema = projection.schema
    assert schema is not None
    assert schema.primary_coordinates == ("x", "primary")
    assert schema.primary_observables == ("alias", "secondary")
    variables = {variable.id: variable for variable in schema.variables}
    assert variables["primary"].role == "coordinate"
    assert variables["primary"].dims == ("point",)
    for record in projected.records:
        assert set(record.coordinates) == {"x", "primary"}
        assert set(record.observables) == {"alias", "secondary"}
        assert record.coordinates["primary"] == record.observables["alias"]


def test_projection_filters_non_coordinate_point_values() -> None:
    scenario, assembled = assembled_measurement_values_for_all_uses()
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    projected = project_measurement_records(
        projection,
        assembled,
        run_id="coordinate-filter-run",
        points=scenario.points,
    )

    assert [record.coordinates for record in projected.records] == [
        {"x": MeasurementScalar.create(dtype="float64", value=0.0)},
        {"x": MeasurementScalar.create(dtype="float64", value=1.0)},
    ]
    assert all("opaque" not in record.coordinates for record in projected.records)


def test_record_metadata_changes_schema_not_product_value_assembly() -> None:
    scenario, assembled = assembled_measurement_values_for_all_uses()
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    assert len(assembled.values) == 6
    first_value = assembled.values[0]
    assert first_value.product.metadata == {"definition": 0}
    assert "projection" not in first_value.product.metadata
    schema = projection.schema
    assert schema is not None
    variables = {variable.id: variable for variable in schema.variables}
    assert variables["primary"].metadata == {
        "definition": 0,
        "projection": "primary",
    }
    assert variables["alias"].metadata == {
        "definition": 0,
        "projection": "alias",
    }
    assert (
        variables["primary"].source_product_id
        == scenario.catalog.product_defs[0].id.qualified_name
    )
    assert (
        variables["alias"].source_product_id == variables["primary"].source_product_id
    )
    assert variables["x"].source_product_id is None


def test_projection_retains_acquisition_evidence_for_each_record_alias() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=2)
    candidates = list(measurement_value_candidates(scenario, scenario.uses))
    evidence = InstrumentAcquisitionEvidence(
        command_id="collect-signal",
        instrument_id="readout",
        interface_id="test.scalar_signal/v1",
        component_path=("channel-1",),
        acquisition_id="sample",
        result_id="signal",
        started_at=datetime(2026, 7, 29, 10, 0, tzinfo=UTC),
        completed_at=datetime(2026, 7, 29, 10, 0, 1, tzinfo=UTC),
    )
    candidates[0] = replace(candidates[0], evidence=evidence)
    assembled = seal_measurement_values(
        scenario.catalog,
        candidates,
        points=scenario.points,
    )
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    projected = project_measurement_records(
        projection,
        assembled,
        run_id="acquisition-evidence-run",
        points=scenario.points,
    )

    evidence_catalog = projected.records[0].acquisition_evidence
    assert evidence_catalog.for_variable("primary") == evidence
    assert evidence_catalog.for_variable("alias") == evidence
    assert len(evidence_catalog.entries) == 1


def test_duplicate_coordinate_rows_keep_distinct_canonical_point_indices() -> None:
    scenario, assembled = assembled_measurement_values_for_all_uses(
        point_values=(4.0, 4.0)
    )
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    projected = project_measurement_records(
        projection,
        assembled,
        run_id="duplicate-coordinate-run",
        points=scenario.points,
    )

    assert [record.point_index for record in projected.records] == [0, 1]
    assert [record.coordinates for record in projected.records] == [
        {"x": MeasurementScalar.create(dtype="float64", value=4.0)},
        {"x": MeasurementScalar.create(dtype="float64", value=4.0)},
    ]
    schema = projection.schema
    assert schema is not None
    assert isinstance(schema.point_domain, MeasurementProductGridPointDomain)
    source = schema.point_domain.axes[0].source
    assert isinstance(source, MeasurementPointDomainValuesSource)
    assert source.values == (
        MeasurementScalar.create(dtype="float64", value=4.0),
        MeasurementScalar.create(dtype="float64", value=4.0),
    )
    assert (
        scenario.bound_points.point_domain.points[0].logical_id
        != scenario.bound_points.point_domain.points[1].logical_id
    )


def test_projection_emits_complete_run_records() -> None:
    scenario = measurement_assembly_scenario(point_values=(0.0,), use_count=2)
    assembled = seal_measurement_values(
        scenario.catalog,
        measurement_value_candidates(scenario, scenario.uses),
        points=scenario.points,
    )
    projection = select_measurement_projection(scenario.catalog, scenario.records)

    projected = project_measurement_records(
        projection,
        assembled,
        run_id="complete-record-run",
        points=scenario.points,
    )

    assert len(projected.records) == 1
    record = projected.records[0]
    assert record.run_id == "complete-record-run"
    assert record.point_index == 0
    assert record.coordinates == {
        "x": MeasurementScalar.create(dtype="float64", value=0.0)
    }
    primary = record.observables["primary"]
    assert isinstance(primary, MeasurementScalar)
    assert primary.dtype == "float64"
    assert primary.unit == "ratio"
    assert primary.value == 0.0


def test_projection_normalizes_compiler_coordinates_to_measurement_scalars() -> None:
    scenario, assembled = assembled_measurement_values_for_all_uses(point_values=(0.0,))
    projection = select_measurement_projection(scenario.catalog, scenario.records)
    original = scenario.points[0]
    cases = (
        (
            Quantity(value=5.0, unit="GHz"),
            MeasurementScalar.create(dtype="float64", unit="GHz", value=5.0),
        ),
        (
            EntityRef(
                id="q0",
                kind="qubit",
                metadata={"label": "readout"},
            ),
            MeasurementScalar.create(
                dtype="string",
                value="q0",
                metadata={
                    "entity": {
                        "kind": "qubit",
                        "metadata": {"label": "readout"},
                    }
                },
            ),
        ),
        (
            EntityRef(id="q0"),
            MeasurementScalar.create(
                dtype="string",
                value="q0",
                metadata={"entity": {}},
            ),
        ),
        (True, MeasurementScalar.create(dtype="bool", value=True)),
        (2, MeasurementScalar.create(dtype="int64", value=2)),
        (2.5, MeasurementScalar.create(dtype="float64", value=2.5)),
        ("high", MeasurementScalar.create(dtype="string", value="high")),
    )

    for source, expected in cases:
        projected = project_measurement_records(
            projection,
            assembled,
            run_id="coordinate-normalization-run",
            points=(AcceptedRunPoint(original.logical_id, {"x": source}),),
        )

        assert projected.records[0].coordinates == {"x": expected}


def test_zero_points_and_no_record_projection_produce_no_measurement_records() -> None:
    zero = measurement_assembly_scenario(point_values=(), use_count=1)
    zero_values = seal_measurement_values(
        zero.catalog,
        (),
        points=zero.points,
    )
    zero_projection = select_measurement_projection(zero.catalog, zero.records)

    assert (
        project_measurement_records(
            zero_projection,
            zero_values,
            run_id="zero-run",
            points=zero.points,
        ).records
        == ()
    )

    no_records = measurement_assembly_scenario(point_values=(0.0, 1.0), use_count=0)
    empty_values = seal_measurement_values(
        no_records.catalog,
        (),
        points=no_records.points,
    )
    empty_projection = select_measurement_projection(
        no_records.catalog, no_records.records
    )

    assert (
        project_measurement_records(
            empty_projection,
            empty_values,
            run_id="no-record-run",
            points=no_records.points,
        ).records
        == ()
    )


def test_projected_recording_contract_matches_bound_projection() -> None:
    _scenario_value, assembled = assembled_measurement_values_for_all_uses()
    projection = select_measurement_projection(
        _scenario_value.catalog, _scenario_value.records
    )

    first = project_measurement_records(
        projection,
        assembled,
        run_id="stable-record-run",
        points=_scenario_value.points,
    )
    assert (
        first.recording_contract_fingerprint
        == projection.recording_contract_fingerprint
    )
