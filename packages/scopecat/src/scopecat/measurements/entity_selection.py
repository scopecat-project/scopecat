"""One ordered entity-axis selection shared by in-memory and bounded readers."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, Field, model_validator

from scopecat.kernel.entity import EntityRef, entity_identity
from scopecat.records.measurement import (
    EntityAcquisitionEvidence,
    MeasurementAcquisitionEvidence,
    MeasurementAcquisitionEvidenceCatalog,
    MeasurementDatasetSchema,
    MeasurementEntityIndex,
    MeasurementEntityProductMetadataOverride,
    MeasurementEntityProductSource,
    MeasurementVariable,
    measurement_result_contract_version,
)


class MeasurementEntitySelection(BaseModel):
    """Select one dimension in request order; absent identities are unavailable."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    dimension_id: str = Field(min_length=1)
    entities: tuple[EntityRef, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_identities(self) -> MeasurementEntitySelection:
        identities = tuple(entity_identity(entity) for entity in self.entities)
        if len(identities) != len(set(identities)):
            raise ValueError("selected entity identities must be unique")
        return self


@dataclass(frozen=True)
class BoundEntitySelection:
    selection: MeasurementEntitySelection
    schema: MeasurementDatasetSchema
    target_to_source: tuple[int | None, ...]
    target_to_schema: tuple[int | None, ...]
    source_count: int

    def evidence(
        self, catalog: MeasurementAcquisitionEvidenceCatalog
    ) -> MeasurementAcquisitionEvidenceCatalog:
        return reindex_entity_evidence(
            catalog,
            dimension_id=self.selection.dimension_id,
            target_to_schema=self.target_to_schema,
        )


def bind_entity_selection(
    schema: MeasurementDatasetSchema,
    selection: MeasurementEntitySelection,
    *,
    source_positions: Sequence[int] | None = None,
) -> BoundEntitySelection:
    dimension = next(
        (item for item in schema.dimensions if item.id == selection.dimension_id), None
    )
    if (
        dimension is None
        or dimension.kind != "entity"
        or not isinstance(dimension.index, MeasurementEntityIndex)
    ):
        raise ValueError(
            f"measurement dimension {selection.dimension_id!r} "
            "is not an indexed entity axis"
        )
    positions = (
        tuple(range(len(dimension.index.values)))
        if source_positions is None
        else tuple(source_positions)
    )
    source = tuple(dimension.index.values[index] for index in positions)
    by_identity = {
        entity_identity(entity): index for index, entity in enumerate(source)
    }
    target_to_source = tuple(
        by_identity.get(entity_identity(entity)) for entity in selection.entities
    )
    target_to_schema = tuple(
        None if index is None else positions[index] for index in target_to_source
    )
    entities = tuple(
        entity if index is None else source[index]
        for entity, index in zip(selection.entities, target_to_source, strict=True)
    )
    dimensions = tuple(
        item.model_copy(
            update={
                "size": len(entities),
                "index": MeasurementEntityIndex(values=entities),
            }
        )
        if item.id == dimension.id
        else item
        for item in schema.dimensions
    )
    variables = tuple(
        reindex_entity_variable_source(
            variable, dimension_id=dimension.id, target_to_schema=target_to_schema
        )
        for variable in schema.variables
    )
    result = schema.result
    if result is not None:
        result = result.model_copy(
            update={
                "version": measurement_result_contract_version(
                    result.id, result.fields, variables=variables, dimensions=dimensions
                )
            }
        )
    return BoundEntitySelection(
        selection=selection.model_copy(update={"entities": entities}),
        schema=schema.model_copy(
            update={"dimensions": dimensions, "variables": variables, "result": result}
        ),
        target_to_source=target_to_source,
        target_to_schema=target_to_schema,
        source_count=len(source),
    )


def reindex_entity_variable_source(
    variable: MeasurementVariable,
    *,
    dimension_id: str,
    target_to_schema: Sequence[int | None],
) -> MeasurementVariable:
    source = variable.source_entity_products
    if source is None or source.dimension_id != dimension_id:
        return variable
    overrides = {item.entity_index: item for item in source.metadata_overrides}
    return variable.model_copy(
        update={
            "source_entity_products": MeasurementEntityProductSource(
                dimension_id=dimension_id,
                product_ids=tuple(
                    None if index is None else source.product_ids[index]
                    for index in target_to_schema
                ),
                common_metadata=source.common_metadata,
                metadata_overrides=tuple(
                    MeasurementEntityProductMetadataOverride(
                        entity_index=target, metadata=overrides[index].metadata
                    )
                    for target, index in enumerate(target_to_schema)
                    if index is not None and index in overrides
                ),
            )
        }
    )


def reindex_entity_evidence(
    catalog: MeasurementAcquisitionEvidenceCatalog,
    *,
    dimension_id: str,
    target_to_schema: Sequence[int | None],
) -> MeasurementAcquisitionEvidenceCatalog:
    by_variable: dict[str, MeasurementAcquisitionEvidence] = {}
    for variable_id in catalog.variable_refs:
        evidence = catalog.for_variable(variable_id)
        if (
            isinstance(evidence, EntityAcquisitionEvidence)
            and evidence.dimension_id == dimension_id
        ):
            evidence = evidence.model_copy(
                update={
                    "values": tuple(
                        None if index is None else evidence.values[index]
                        for index in target_to_schema
                    )
                }
            )
        if evidence is not None:
            by_variable[variable_id] = evidence
    return MeasurementAcquisitionEvidenceCatalog.create(by_variable)
