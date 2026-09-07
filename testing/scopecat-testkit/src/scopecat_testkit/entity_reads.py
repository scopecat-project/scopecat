"""Generated wide entity data for bounded-read qualification; no device runtime."""

from datetime import UTC, datetime

import numpy as np
from scopecat.kernel.entity import EntityRef
from scopecat.records.measurement import (
    EntityAcquisitionEvidence,
    InstrumentAcquisitionEvidence,
    MeasurementAcquisitionEvidenceCatalog,
    MeasurementArray,
    MeasurementArrayAvailability,
    MeasurementDataset,
    MeasurementDatasetSchema,
    MeasurementDimension,
    MeasurementEntityAcquisition,
    MeasurementEntityIndex,
    MeasurementEntityProductSource,
    MeasurementPointCloudPointDomain,
    MeasurementRecord,
    MeasurementVariable,
)


def wide_entity_measurements(
    *,
    run_id: str = "run-wide",
    entity_order: tuple[int, ...] = tuple(range(8)),
    point_count: int = 2,
    sample_count: int = 16,
    complex_values: bool = False,
) -> MeasurementDataset:
    entities = tuple(
        EntityRef(
            kind="qubit", id=f"q{index}", metadata={"label": f"{run_id}:Q{index}"}
        )
        for index in entity_order
    )
    acquisition = MeasurementEntityAcquisition()
    dtype = "complex128" if complex_values else "float64"
    schema = MeasurementDatasetSchema(
        dataset_id="raw-measurements",
        point_domain=MeasurementPointCloudPointDomain(columns=()),
        dimensions=(
            MeasurementDimension(id="point", kind="point", size=point_count),
            MeasurementDimension(
                id="entity",
                kind="entity",
                size=len(entities),
                index=MeasurementEntityIndex(values=entities),
            ),
            MeasurementDimension(id="sample", kind="local", size=sample_count),
        ),
        variables=(
            MeasurementVariable(
                id="time",
                role="coordinate",
                dtype="float64",
                unit="s",
                dims=("point", "sample"),
            ),
            MeasurementVariable(
                id="signal",
                role="observable",
                dtype=dtype,
                unit="V",
                dims=("point", "entity", "sample"),
                entity_acquisition=acquisition,
                source_entity_products=MeasurementEntityProductSource(
                    dimension_id="entity",
                    product_ids=tuple(f"{run_id}/{entity.id}" for entity in entities),
                ),
            ),
        ),
        primary_observables=("signal",),
    )
    now = datetime(2026, 9, 8, tzinfo=UTC)
    evidence = EntityAcquisitionEvidence(
        dimension_id="entity",
        acquisition=acquisition,
        values=tuple(
            InstrumentAcquisitionEvidence(
                command_id=f"{run_id}-{entity.id}",
                instrument_id="virtual",
                interface_id="test.wide/v1",
                acquisition_id="acquire",
                result_id=entity.id,
                started_at=now,
                completed_at=now,
            )
            for entity in entities
        ),
    )
    records = []
    for point in range(point_count):
        values = (
            np.asarray(entity_order, dtype=np.float64)[:, None] * 100
            + np.arange(sample_count, dtype=np.float64)[None, :]
            + point
        )
        if complex_values:
            values = values + 1j * values
        valid = np.ones(values.shape, dtype=np.bool_)
        valid[0, -1] = False
        records.append(
            MeasurementRecord(
                run_id=run_id,
                point_index=point,
                logical_point_id=f"point-{point}",
                coordinates={
                    "time": MeasurementArray.create(
                        values=np.arange(sample_count, dtype=np.float64), unit="s"
                    )
                },
                observables={
                    "signal": MeasurementArray.create(
                        values=values,
                        dtype=dtype,
                        unit="V",
                        availability=MeasurementArrayAvailability.create(
                            valid=valid,
                            reason="overload",
                            metadata={"detector": entities[0].id},
                        ),
                    )
                },
                acquisition_evidence=MeasurementAcquisitionEvidenceCatalog.create(
                    {"signal": evidence}
                ),
            )
        )
    return MeasurementDataset(
        dataset_schema=schema,
        records=tuple(records),
        metadata={"source": "generated-wide-data"},
    )
