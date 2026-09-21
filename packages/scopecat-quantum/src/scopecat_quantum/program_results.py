"""Map prepared target results to logical domain outputs."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace

from scopecat.measurements.products import ProductDef
from scopecat.sdk.domain import (
    DomainBatchRequest,
    DomainMappedResult,
    DomainPointRef,
    DomainPreparationBuilder,
    DomainProductUseRef,
    DomainResultBinding,
    DomainResultMapping,
)

from scopecat_quantum._ids import TargetCompileEntryId
from scopecat_quantum.acquisitions import (
    QuantumResultContract,
    QuantumResultDimension,
)
from scopecat_quantum.program_targets import PreparedQuantumTargetBatch
from scopecat_quantum.pulses import AcquisitionSlot
from scopecat_quantum.targets import (
    TargetAcquisitionAddress,
    TargetArtifact,
)


@dataclass(frozen=True, slots=True)
class QuantumTargetEntryPointBinding:
    """Adapter edge from one mixed-program target entry to one logical point."""

    entry_id: TargetCompileEntryId
    point: DomainPointRef


@dataclass(frozen=True, slots=True)
class QuantumTargetResultAddress:
    """One logical result assembled from one or more target acquisitions."""

    acquisitions: tuple[TargetAcquisitionAddress, ...]

    def __post_init__(self) -> None:
        if not self.acquisitions:
            raise ValueError("quantum result addresses require acquisitions")
        if len(set(self.acquisitions)) != len(self.acquisitions):
            raise ValueError("quantum result addresses require unique acquisitions")
        if len({address.entry_id for address in self.acquisitions}) != 1:
            raise ValueError("one quantum result address must belong to one entry")

    @property
    def entry_id(self) -> TargetCompileEntryId:
        """Return the target entry shared by every acquisition in the group."""

        return self.acquisitions[0].entry_id


@dataclass(frozen=True, slots=True)
class QuantumTargetResultUseBinding:
    """Adapter edge from one acquisition to one logical product use."""

    address: QuantumTargetResultAddress
    product_use: DomainProductUseRef


@dataclass(frozen=True, slots=True)
class MappedQuantumTarget[ArtifactT: TargetArtifact]:
    """One opaque target artifact and its exact logical result mapping."""

    artifact: ArtifactT
    mapping: DomainResultMapping[QuantumTargetResultAddress]

    @property
    def acquisition_addresses(self) -> tuple[TargetAcquisitionAddress, ...]:
        """Return raw target addresses in logical result order."""

        return tuple(
            address
            for result in self.mapping.results
            for address in result.result_address.acquisitions
        )


def map_quantum_target_results(
    preparation: DomainPreparationBuilder,
    batch: PreparedQuantumTargetBatch,
    entry_bindings: Sequence[QuantumTargetEntryPointBinding],
) -> DomainResultMapping[QuantumTargetResultAddress]:
    """Map acquisitions whose local slot names are logical result IDs.

    Recipe lowering preserves those names, including entity-qualified slots.
    Grouped acquisitions retain target order. Adapters still explicitly bind
    entries to points; no positional correspondence is assumed. Targets that
    rename or otherwise regroup results can use the explicit sealing API.
    """

    bindings = tuple(
        QuantumTargetResultUseBinding(
            QuantumTargetResultAddress(
                tuple(
                    address
                    for address in entry.acquisition_addresses
                    if address.slot_id.local_id == result.id
                )
            ),
            product_use,
        )
        for entry in batch.entries
        for result in preparation.context.call.results
        for product_use in result.product_uses
    )
    return seal_quantum_target_result_mapping(
        preparation, batch, entry_bindings, bindings
    )


def seal_quantum_target_result_mapping(
    preparation: DomainPreparationBuilder,
    batch: PreparedQuantumTargetBatch,
    entry_bindings: Sequence[QuantumTargetEntryPointBinding],
    result_bindings: Sequence[QuantumTargetResultUseBinding],
) -> DomainResultMapping[QuantumTargetResultAddress]:
    """Close exact target entry/result coverage against logical outputs."""

    selected_entry_bindings = tuple(entry_bindings)
    selected_result_bindings = tuple(result_bindings)
    point_by_entry = {
        binding.entry_id: binding.point for binding in selected_entry_bindings
    }
    expected_entry_ids = batch.request.source_entry_ids
    if len(point_by_entry) != len(selected_entry_bindings) or set(
        point_by_entry
    ) != set(expected_entry_ids):
        raise ValueError("quantum entry-point bindings must cover target entries")
    domain_mapping = preparation.map_measurements(
        results=tuple(
            DomainResultBinding(
                result_address=binding.address,
                point=point_by_entry[binding.address.entry_id],
                product_use=binding.product_use,
            )
            for binding in selected_result_bindings
        )
    )
    expected_addresses = batch.request.acquisition_addresses
    mapped_addresses = tuple(
        address
        for result in domain_mapping.results
        for address in result.result_address.acquisitions
    )
    if len(mapped_addresses) != len(expected_addresses) or set(mapped_addresses) != set(
        expected_addresses
    ):
        raise ValueError(
            "domain mapping must exactly cover prepared acquisition addresses"
        )
    _validate_quantum_result_contracts(
        preparation.context,
        batch,
        domain_mapping.results,
    )
    return domain_mapping


def _validate_quantum_result_contracts(
    context: DomainBatchRequest,
    batch: PreparedQuantumTargetBatch,
    results: tuple[DomainMappedResult[QuantumTargetResultAddress], ...],
) -> None:
    slot_by_address = {
        TargetAcquisitionAddress(entry.id, slot.id): slot
        for entry in batch.request.entries
        for slot in entry.program.acquisition_slots
    }
    point_index_by_identity = {
        id(point): index for index, point in enumerate(context.points)
    }
    for result in results:
        source_contract = _source_result_contract(context, result)
        point_index = point_index_by_identity[id(result.point)]
        concrete_contract = _concrete_contract_at_point(
            source_contract,
            context,
            point_index,
        )
        slots = tuple(
            slot_by_address[address] for address in result.result_address.acquisitions
        )
        if any(slot.contract != concrete_contract for slot in slots):
            raise ValueError(
                "target acquisition slot contract must match the point-bound "
                "logical quantum result contract"
            )
        _validate_product_contract(
            result.product,
            source_contract=source_contract,
            concrete_contract=concrete_contract,
            slots=slots,
            repetitions=batch.request.repetitions,
        )


def _source_result_contract(
    context: DomainBatchRequest,
    result: DomainMappedResult[QuantumTargetResultAddress],
) -> QuantumResultContract:
    mapped_use_ids = {product_use.id for product_use in result.product_uses}
    candidates = tuple(
        logical_result
        for logical_result in context.call.results
        if any(
            product_use.id in mapped_use_ids
            for product_use in logical_result.product_uses
        )
    )
    if len(candidates) != 1:
        raise ValueError(
            "mapped quantum result must resolve to exactly one logical result contract"
        )
    source = candidates[0].contract
    if isinstance(source, QuantumResultContract):
        return source
    nested = getattr(source, "contract", None)
    if isinstance(nested, QuantumResultContract):
        return nested
    raise ValueError("logical quantum results require a QuantumResultContract")


def _concrete_contract_at_point(
    contract: QuantumResultContract,
    context: DomainBatchRequest,
    point_index: int,
) -> QuantumResultContract:
    if contract.is_concrete:
        return contract
    dimensions: list[QuantumResultDimension] = []
    for dimension in contract.dimensions:
        input_id = dimension.size_input_id
        if input_id is None:
            dimensions.append(dimension)
            continue
        try:
            selected = context.inputs.program_input(input_id)[point_index]
        except KeyError as error:
            raise ValueError(
                f"logical result dimension {dimension.id!r} references missing "
                f"program input {input_id!r}"
            ) from error
        if (
            not isinstance(selected, int)
            or isinstance(selected, bool)
            or not dimension.minimum_size <= selected <= dimension.maximum_size
        ):
            raise ValueError(
                f"logical result dimension {dimension.id!r} input {input_id!r} "
                f"must resolve within [{dimension.minimum_size}, "
                f"{dimension.maximum_size}]"
            )
        dimensions.append(replace(dimension, size=selected))
    return replace(contract, dimensions=tuple(dimensions))


def _validate_product_contract(
    product: ProductDef,
    *,
    source_contract: QuantumResultContract,
    concrete_contract: QuantumResultContract,
    slots: tuple[AcquisitionSlot, ...],
    repetitions: int,
) -> None:
    if product.dtype != source_contract.dtype or product.unit != source_contract.unit:
        raise ValueError(
            "logical product dtype/unit must match its quantum result contract"
        )
    if product.metadata.get("quantum.acquisition_kind") != (
        source_contract.acquisition_kind.value
    ):
        raise ValueError(
            "logical product acquisition kind must match its quantum result contract"
        )

    axes = list(product.axes)
    if axes and axes[0].kind == "entity":
        entity = axes.pop(0)
        if (
            entity.id != "entity"
            or entity.unit is not None
            or entity.size != len(slots)
        ):
            raise ValueError(
                "logical product entity axis must match grouped acquisition slots"
            )
    elif len(slots) != 1:
        raise ValueError(
            "logical products without an entity axis require one acquisition slot"
        )

    if not axes:
        raise ValueError("logical quantum products require a shot axis")
    shot = axes.pop(0)
    if (
        shot.id != "shot"
        or shot.kind != "shot"
        or shot.unit != "count"
        or shot.size != repetitions
    ):
        raise ValueError(
            "logical product shot axis must match target batch repetitions"
        )

    if len(axes) != len(source_contract.dimensions):
        raise ValueError(
            "logical product local axes must match its quantum result dimensions"
        )
    for axis, source_dimension, concrete_dimension in zip(
        axes,
        source_contract.dimensions,
        concrete_contract.dimensions,
        strict=True,
    ):
        size_matches = (
            axis.size == source_dimension.size
            if isinstance(source_dimension.size, int)
            else axis.size is None or axis.size == concrete_dimension.size
        )
        if (
            axis.id != source_dimension.id
            or axis.kind != source_dimension.kind
            or axis.unit != source_dimension.unit
            or not size_matches
        ):
            raise ValueError(
                "logical product local axes must match its quantum result dimensions"
            )
