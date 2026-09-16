"""Independent RecordUse projection over assembled host measurement values."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal, cast

import numpy as np

from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.graph_identity import ValueId
from scopecat.kernel.point_identity import LogicalPointId
from scopecat.kernel.points import AcceptedRunPoint
from scopecat.kernel.problems import Problem, ProblemPhase, model_location, problem
from scopecat.kernel.product_identity import ProductId, ProductUse, ProductUseId
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_data import CellValue
from scopecat.measurements.products import ProductDef
from scopecat.measurements.records import (
    BoundRecordUse,
    DatasetRecordPlan,
    EntityAcquisitionCohortPlan,
    EntityRecordPlan,
    EntityRecordUse,
    ProductRecordUse,
    RecordPlan,
    RecordUse,
    ValueRecordCandidate,
    ValueRecordPlan,
    ValueRecordUse,
    expected_dataset_schema,
    measurement_scalar,
    plan_entity_acquisition_cohorts,
    plan_records,
    plan_value_records,
    validate_record_plan,
)
from scopecat.measurements.values import (
    ClosedMeasurementProductValues,
    MeasurementValueCatalog,
)
from scopecat.program.measurement_types import MeasurementVariableRole
from scopecat.program.recording import ExperimentResultField
from scopecat.records.measurement import (
    EntityAcquisitionEvidence,
    InstrumentAcquisitionEvidence,
    MeasurementAcquisitionEvidenceCatalog,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementArrayUnavailableGroup,
    MeasurementDatasetSchema,
    MeasurementEntityAcquisition,
    MeasurementPartitionedArray,
    MeasurementRecord,
    MeasurementScalar,
    MeasurementSegmentedArray,
    MeasurementUnavailable,
    MeasurementValue,
)

type StaticValueCandidateSource = Callable[
    [Sequence[AcceptedRunPoint]],
    tuple[ValueRecordCandidate, ...],
]


def _no_static_values(
    _points: Sequence[AcceptedRunPoint],
) -> tuple[ValueRecordCandidate, ...]:
    return ()


@dataclass(frozen=True, slots=True, init=False)
class MeasurementProjection:
    """Closed pre-effect plan for durable measurement variables."""

    catalog: MeasurementValueCatalog = field(repr=False)
    _records: tuple[DatasetRecordPlan, ...] = field(repr=False)
    _acquisition_cohorts: tuple[EntityAcquisitionCohortPlan, ...] = field(repr=False)
    _static_value_source: StaticValueCandidateSource = field(
        repr=False,
        compare=False,
    )
    _result_fields: tuple[ExperimentResultField, ...] = field(
        repr=False,
        compare=False,
    )
    contract_fingerprint: str

    def __init__(
        self,
        catalog: MeasurementValueCatalog,
        records: tuple[DatasetRecordPlan, ...],
        *,
        static_value_source: StaticValueCandidateSource = _no_static_values,
        result_fields: Sequence[ExperimentResultField] = (),
    ) -> None:
        object.__setattr__(self, "catalog", catalog)
        object.__setattr__(self, "_records", records)
        object.__setattr__(
            self,
            "_acquisition_cohorts",
            plan_entity_acquisition_cohorts(records),
        )
        object.__setattr__(
            self,
            "_static_value_source",
            static_value_source,
        )
        object.__setattr__(self, "_result_fields", tuple(result_fields))
        object.__setattr__(
            self,
            "contract_fingerprint",
            _projection_contract_fingerprint(
                catalog.contract_fingerprint,
                records,
                catalog.point_contract.coordinate_ids,
            ),
        )

    @property
    def records(self) -> tuple[DatasetRecordPlan, ...]:
        return self._records

    @property
    def acquisition_cohorts(self) -> tuple[EntityAcquisitionCohortPlan, ...]:
        return self._acquisition_cohorts

    @property
    def runtime_value_ids(self) -> tuple[ValueId, ...]:
        return tuple(
            dict.fromkeys(
                record.value_id
                for record in self.records
                if isinstance(record, ValueRecordPlan) and record.requires_execution
            )
        )

    def static_value_candidates(
        self,
        points: Sequence[AcceptedRunPoint],
    ) -> tuple[ValueRecordCandidate, ...]:
        """Materialize static recorded values only for one execution coverage."""

        return self._static_value_source(points)

    @property
    def coordinate_ids(self) -> tuple[str, ...]:
        return self.catalog.point_contract.coordinate_ids

    @property
    def has_dataset(self) -> bool:
        """Whether selected records or returned point coordinates require data."""

        coordinate_ids = frozenset(self.coordinate_ids)
        return bool(self.records) or any(
            field.variable_id in coordinate_ids for field in self._result_fields
        )

    @property
    def schema(self) -> MeasurementDatasetSchema | None:
        """Return the complete planned dataset schema for this projection."""

        if not self.has_dataset:
            return None
        return expected_dataset_schema(
            experiment_id=self.catalog.point_contract.experiment_id,
            point_count=self.catalog.point_contract.point_count,
            records=self.records,
            point_coordinate_columns=self.catalog.point_contract.coordinate_columns,
            point_domain_layout=(
                "point_cloud"
                if self.catalog.point_contract.open_length
                else self.catalog.point_contract.domain_layout
            ),
            result_fields=self._result_fields,
            point_domain_axes=self.catalog.point_contract.domain_axes,
        )

    @property
    def recording_contract_fingerprint(self) -> str:
        """Identify the durable recording shape across equivalent replans."""

        return stable_content_hash(
            content_fingerprint(
                {
                    "schema": "scopecat.measurement_recording_contract.v1",
                    "dataset_schema": self.schema,
                }
            )
        )


@dataclass(frozen=True, slots=True, init=False)
class ProjectedMeasurementDataset:
    """Canonical measurement records for one projection and point range."""

    projection: MeasurementProjection = field(repr=False)
    run_id: str
    _records: tuple[MeasurementRecord, ...] = field(repr=False)

    def __init__(
        self,
        projection: MeasurementProjection,
        run_id: str,
        records: tuple[MeasurementRecord, ...],
    ) -> None:
        object.__setattr__(self, "projection", projection)
        object.__setattr__(self, "run_id", run_id)
        object.__setattr__(self, "_records", tuple(records))

    @property
    def records(self) -> tuple[MeasurementRecord, ...]:
        return self._records

    @property
    def recording_contract_fingerprint(self) -> str:
        """Return the pre-value contract shared by every record chunk."""

        return self.projection.recording_contract_fingerprint


def select_measurement_projection(
    catalog: MeasurementValueCatalog,
    record_uses: Sequence[BoundRecordUse],
    *,
    static_value_source: StaticValueCandidateSource = _no_static_values,
    result_fields: Sequence[ExperimentResultField] = (),
) -> MeasurementProjection:
    """Close every record projection against one value catalog."""

    selected_records = tuple(record_uses)
    product_records = tuple(
        record
        for record in selected_records
        if isinstance(record, RecordUse | EntityRecordUse)
    )
    value_records = tuple(
        record for record in selected_records if isinstance(record, ValueRecordUse)
    )
    product_uses = catalog.product_uses
    product_defs = catalog.product_defs
    use_by_id = {use.id: use for use in product_uses}
    product_by_id = {product.id: product for product in product_defs}
    problems: list[Problem] = []

    for index, record in enumerate(product_records):
        if not _record_product_exists(record, use_by_id, product_by_id):
            problems.append(
                _projection_problem(
                    "measurement_projection_product_missing",
                    f"record {record.id!r} does not resolve to a logical product",
                    path=("records", index, "product_use_id"),
                )
            )
    if problems:
        raise CheckFailed(problems)

    coordinate_ids = catalog.point_contract.coordinate_ids
    product_record_iterator = iter(
        plan_records(
            product_defs,
            product_uses,
            product_records,
        )
    )
    value_record_iterator = iter(plan_value_records(value_records))
    record_plans: tuple[DatasetRecordPlan, ...] = tuple(
        next(value_record_iterator)
        if isinstance(record, ValueRecordUse)
        else next(product_record_iterator)
        for record in selected_records
    )
    record_problems = validate_record_plan(
        record_plans,
        coordinate_ids=coordinate_ids,
    )
    if record_problems:
        raise CheckFailed(record_problems)
    return MeasurementProjection(
        catalog,
        record_plans,
        static_value_source=static_value_source,
        result_fields=result_fields,
    )


def project_measurement_records(
    projection: MeasurementProjection,
    product_values: ClosedMeasurementProductValues,
    *,
    run_id: str,
    points: Sequence[AcceptedRunPoint],
    value_candidates: Sequence[ValueRecordCandidate] = (),
) -> ProjectedMeasurementDataset:
    """Project one closed admitted point range without changing product values."""

    if not run_id:
        msg = "measurement projection run_id must be non-empty"
        raise ValueError(msg)
    values = product_values
    if values.catalog.contract_fingerprint != projection.catalog.contract_fingerprint:
        msg = "assembled measurement values do not belong to this projection"
        raise ValueError(msg)
    record_plans = projection.records
    points = tuple(points)
    _validate_entity_acquisition_cohorts(
        projection.acquisition_cohorts,
        values,
        points=points,
    )
    value_candidates_by_key = {
        (candidate.logical_point_id, candidate.value_id): candidate.value
        for candidate in value_candidates
    }
    if not projection.has_dataset:
        records: tuple[MeasurementRecord, ...] = ()
    else:
        records = tuple(
            MeasurementRecord(
                run_id=run_id,
                logical_point_id=point.logical_id.value,
                point_index=point.logical_ordinal,
                coordinates={
                    **_point_coordinates(point.row, projection.coordinate_ids),
                    **_projected_values(
                        record_plans,
                        role="coordinate",
                        product_values=values,
                        value_candidates=value_candidates_by_key,
                        point=point,
                    ),
                },
                observables=_projected_values(
                    record_plans,
                    role="observable",
                    product_values=values,
                    value_candidates=value_candidates_by_key,
                    point=point,
                ),
                acquisition_evidence=MeasurementAcquisitionEvidenceCatalog.create(
                    _projected_acquisition_evidence(
                        record_plans,
                        product_values=values,
                        point=point,
                    )
                ),
            )
            for point in points
        )
    return ProjectedMeasurementDataset(
        projection,
        run_id,
        records,
    )


def _projection_contract_fingerprint(
    catalog_fingerprint: str,
    records: Sequence[DatasetRecordPlan],
    coordinate_ids: Sequence[str],
) -> str:
    return stable_content_hash(
        content_fingerprint(
            {
                "schema": "scopecat.measurements.projection_contract.v6",
                "catalog_fingerprint": catalog_fingerprint,
                "records": tuple(_record_contract(record) for record in records),
                "coordinate_ids": tuple(coordinate_ids),
            }
        )
    )


def _record_contract(record: DatasetRecordPlan) -> object:
    if isinstance(record, RecordPlan | EntityRecordPlan):
        return record
    return {
        "kind": "value",
        "id": record.id,
        "source_value_id": record.source_value_id,
        "dtype": record.dtype,
        "requires_execution": record.requires_execution,
        "role": record.role,
        "unit": record.unit,
        "metadata": record.metadata,
    }


def _projected_values(
    records: Sequence[DatasetRecordPlan],
    *,
    role: MeasurementVariableRole,
    product_values: ClosedMeasurementProductValues,
    value_candidates: Mapping[tuple[LogicalPointId, ValueId], object],
    point: AcceptedRunPoint,
) -> dict[str, MeasurementValue]:
    projected: dict[str, MeasurementValue] = {}
    for record in records:
        if record.role != role:
            continue
        if isinstance(record, RecordPlan):
            projected[record.id] = product_values.value_for_output(
                point.logical_id,
                record.product_use_id,
            ).value
            continue
        if isinstance(record, EntityRecordPlan):
            projected[record.id] = _projected_entity_value(
                record,
                product_values=product_values,
                point=point,
            )
            continue
        try:
            value = value_candidates[(point.logical_id, record.value_id)]
        except KeyError as error:
            raise ValueError(
                f"recorded value {record.id!r} is unavailable for point "
                f"{point.logical_ordinal}"
            ) from error
        projected[record.id] = (
            MeasurementArray.create(
                values=value,
                dtype=record.dtype,
                unit=record.unit,
            )
            if record.axes
            else (
                measurement_scalar(value)
                if isinstance(value, Quantity | EntityRef)
                else MeasurementScalar.create(
                    value=value, dtype=record.dtype, unit=record.unit
                )
            )
        )
    return projected


def _projected_entity_value(
    record: EntityRecordPlan,
    *,
    product_values: ClosedMeasurementProductValues,
    point: AcceptedRunPoint,
) -> MeasurementValue:
    values = tuple(
        (
            value.materialize()
            if isinstance(value, MeasurementPartitionedArray)
            else value
        )
        for member in record.members
        for value in (
            product_values.value_for_output(
                point.logical_id,
                member.product_use_id,
            ).value,
        )
    )
    if _entity_local_shapes_vary(values):
        return _projected_entity_ragged_value(record, values)
    local_shape = _entity_value_local_shape(values)
    fully_available = tuple(
        _fully_available_entity_chunk(value, dtype=record.dtype) for value in values
    )
    if all(chunk is not None for chunk in fully_available):
        available_chunks = cast("tuple[np.ndarray, ...]", fully_available)
        if any(chunk.shape != local_shape for chunk in available_chunks):
            raise ValueError(
                f"entity record {record.id!r} has inconsistent point-local shapes"
            )
        return MeasurementArray.create(
            values=np.stack(available_chunks, axis=0),
            dtype=record.dtype,
            unit=record.unit,
        )

    local_size = int(np.prod(local_shape, dtype=np.int64))
    chunks: list[np.ndarray] = []
    valid_chunks: list[np.ndarray] = []
    unavailable_groups: list[MeasurementArrayUnavailableGroup] = []
    for entity_index, value in enumerate(values):
        if isinstance(value, MeasurementUnavailable):
            chunks.append(
                np.zeros(local_shape, dtype=_measurement_numpy_dtype(record.dtype))
            )
            valid_chunks.append(np.zeros(local_shape, dtype=np.bool_))
            unavailable_groups.append(
                MeasurementArrayUnavailableGroup(
                    reason=value.reason,
                    flat_indices=tuple(
                        range(
                            entity_index * local_size, (entity_index + 1) * local_size
                        )
                    ),
                    metadata=value.metadata,
                )
            )
            continue
        chunk = (
            np.asarray(value.value, dtype=_measurement_numpy_dtype(record.dtype))
            if isinstance(value, MeasurementScalar)
            else value.values
        )
        if chunk.shape != local_shape:
            raise ValueError(
                f"entity record {record.id!r} has inconsistent point-local shapes"
            )
        chunks.append(chunk)
        if isinstance(value, MeasurementArray) and value.availability is not None:
            valid_chunks.append(value.availability.valid)
            for group in value.availability.unavailable:
                unavailable_groups.append(
                    MeasurementArrayUnavailableGroup(
                        reason=group.reason,
                        flat_indices=tuple(
                            entity_index * local_size + index
                            for index in group.flat_indices
                        ),
                        metadata=group.metadata,
                    )
                )
        else:
            valid_chunks.append(np.ones(local_shape, dtype=np.bool_))

    stacked = np.stack(chunks, axis=0)
    valid = np.stack(valid_chunks, axis=0)
    availability = (
        None
        if bool(np.all(valid))
        else MeasurementArrayAvailability(
            valid=valid,
            unavailable=_merge_unavailable_groups(unavailable_groups),
        )
    )
    return MeasurementArray.create(
        values=stacked,
        dtype=record.dtype,
        unit=record.unit,
        availability=availability,
    )


def _entity_local_shapes_vary(values: Sequence[MeasurementValue]) -> bool:
    shapes = tuple(
        () if isinstance(value, MeasurementScalar) else tuple(value.shape)
        for value in values
        if not isinstance(value, MeasurementUnavailable)
        or all(extent is not None for extent in value.shape)
    )
    return len(set(shapes)) > 1


def _projected_entity_ragged_value(
    record: EntityRecordPlan,
    values: Sequence[MeasurementValue],
) -> MeasurementSegmentedArray:
    if any(
        isinstance(value, MeasurementScalar | MeasurementSegmentedArray)
        for value in values
    ):
        raise ValueError(
            f"entity record {record.id!r} ragged members must be local arrays"
        )
    segments = cast(
        "tuple[MeasurementArray | MeasurementUnavailable, ...]",
        tuple(values),
    )
    expected_axes = record.axes[1:]
    if not expected_axes or all(axis.size is not None for axis in expected_axes):
        raise ValueError(
            f"entity record {record.id!r} has inconsistent fixed local shapes"
        )
    for segment in segments:
        shape = segment.shape
        if len(shape) != len(expected_axes) or any(
            axis.size is not None
            and shape[index] is not None
            and axis.size != shape[index]
            for index, axis in enumerate(expected_axes)
        ):
            raise ValueError(
                f"entity record {record.id!r} has an incompatible ragged shape"
            )
    return MeasurementSegmentedArray.create(
        segments=segments,
        dtype=record.dtype,
        unit=record.unit,
    )


def _fully_available_entity_chunk(
    value: MeasurementValue,
    *,
    dtype: str,
) -> np.ndarray | None:
    if isinstance(value, MeasurementUnavailable) or (
        isinstance(value, MeasurementArray) and value.availability is not None
    ):
        return None
    if isinstance(value, MeasurementScalar):
        return np.asarray(value.value, dtype=_measurement_numpy_dtype(dtype))
    return value.values


def _merge_unavailable_groups(
    groups: Sequence[MeasurementArrayUnavailableGroup],
) -> tuple[MeasurementArrayUnavailableGroup, ...]:
    merged: dict[str, MeasurementArrayUnavailableGroup] = {}
    for group in groups:
        key = stable_content_hash(
            content_fingerprint({"reason": group.reason, "metadata": group.metadata})
        )
        existing = merged.get(key)
        if existing is None:
            merged[key] = group
            continue
        if existing.reason != group.reason or existing.metadata != group.metadata:
            raise AssertionError("availability diagnostic hash collision")
        merged[key] = existing.model_copy(
            update={"flat_indices": (*existing.flat_indices, *group.flat_indices)}
        )
    return tuple(merged.values())


def _validate_entity_acquisition_cohorts(
    cohorts: Sequence[EntityAcquisitionCohortPlan],
    values: ClosedMeasurementProductValues,
    *,
    points: Sequence[AcceptedRunPoint],
) -> None:
    for point in points:
        for cohort in cohorts:
            selected = tuple(
                values.value_for_output(point.logical_id, product_use_id)
                for member in cohort.members
                for product_use_id in member.product_use_ids
            )
            evidence = tuple(
                item.evidence for item in selected if item.evidence is not None
            )
            if cohort.policy == "all_or_nothing":
                outcomes = tuple(
                    _entity_acquisition_outcome(item.value) for item in selected
                )
                if not (
                    all(outcome == "available" for outcome in outcomes)
                    or all(outcome == "unavailable" for outcome in outcomes)
                ):
                    raise ValueError(
                        f"entity acquisition cohort {cohort.id!r} violates "
                        "all_or_nothing acquisition semantics"
                    )
            if evidence and len({item.command_id for item in evidence}) != 1:
                raise ValueError(
                    f"entity acquisition cohort {cohort.id!r} spans multiple "
                    "hardware commands"
                )


def _entity_acquisition_outcome(
    value: MeasurementValue,
) -> Literal["available", "unavailable", "partial"]:
    if isinstance(value, MeasurementUnavailable):
        return "unavailable"
    if isinstance(value, MeasurementArray | MeasurementPartitionedArray) and (
        value.availability is not None
    ):
        return "partial"
    return "available"


def _entity_value_local_shape(values: Sequence[MeasurementValue]) -> tuple[int, ...]:
    concrete = tuple(
        ()
        if isinstance(value, MeasurementScalar)
        else cast("tuple[int, ...]", tuple(value.shape))
        for value in values
        if not isinstance(value, MeasurementUnavailable)
    )
    if concrete:
        first = concrete[0]
        if any(shape != first for shape in concrete):
            raise ValueError("entity record members have inconsistent shapes")
        return first
    unavailable_shapes = tuple(
        tuple(value.shape)
        for value in values
        if isinstance(value, MeasurementUnavailable)
    )
    first_unavailable = unavailable_shapes[0]
    if any(shape != first_unavailable for shape in unavailable_shapes) or any(
        extent is None for extent in first_unavailable
    ):
        raise ValueError("entity record unavailable members need one concrete shape")
    return cast("tuple[int, ...]", first_unavailable)


def _measurement_numpy_dtype(dtype: str) -> np.dtype:
    return np.dtype(
        {
            "bool": np.bool_,
            "int64": np.int64,
            "float64": np.float64,
            "complex128": np.complex128,
            "string": np.str_,
        }[dtype]
    )


def _projected_acquisition_evidence(
    records: Sequence[DatasetRecordPlan],
    *,
    product_values: ClosedMeasurementProductValues,
    point: AcceptedRunPoint,
) -> dict[str, InstrumentAcquisitionEvidence | EntityAcquisitionEvidence]:
    acquisition_evidence: dict[
        str, InstrumentAcquisitionEvidence | EntityAcquisitionEvidence
    ] = {}
    for record in records:
        if isinstance(record, EntityRecordPlan):
            evidence = tuple(
                product_values.value_for_output(
                    point.logical_id,
                    member.product_use_id,
                ).evidence
                for member in record.members
            )
            if any(item is not None for item in evidence):
                acquisition_evidence[record.id] = EntityAcquisitionEvidence(
                    dimension_id=record.axes[0].id,
                    acquisition=MeasurementEntityAcquisition(
                        policy=record.acquisition.policy,
                        cohort_id=record.acquisition.cohort_id,
                    ),
                    values=evidence,
                )
            continue
        if not isinstance(record, RecordPlan):
            continue
        evidence = product_values.value_for_output(
            point.logical_id,
            record.product_use_id,
        ).evidence
        if evidence is not None:
            acquisition_evidence[record.id] = evidence
    return acquisition_evidence


def _record_product_exists(
    record: ProductRecordUse,
    use_by_id: Mapping[ProductUseId, ProductUse],
    product_by_id: Mapping[ProductId, ProductDef],
) -> bool:
    use_ids = (
        (record.product_use_id,)
        if isinstance(record, RecordUse)
        else tuple(member.product_use_id for member in record.members)
    )
    return all(
        (use := use_by_id.get(use_id)) is not None and use.product_id in product_by_id
        for use_id in use_ids
    )


def _point_coordinates(
    row: Mapping[str, CellValue],
    coordinate_ids: Sequence[str],
) -> dict[str, MeasurementValue]:
    return {
        coordinate_id: measurement_scalar(row[coordinate_id])
        for coordinate_id in coordinate_ids
    }


def _projection_problem(
    code: str,
    message: str,
    *,
    path: tuple[str | int, ...],
) -> Problem:
    return problem(
        code,
        message,
        phase=ProblemPhase.PLANNING,
        location=model_location("measurement_projection", *path),
    )
