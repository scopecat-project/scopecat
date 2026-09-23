"""Builder facade for closing one selected domain batch before effects."""

from __future__ import annotations

from collections.abc import Callable, Hashable, Iterable, Sequence
from types import MappingProxyType
from typing import cast

from scopecat.inspection import (
    CompiledArtifactInspection,
    CompiledProgramInspectionQuery,
)
from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.kernel.product_identity import ProductId, ProductUseId
from scopecat.kernel.state import PayloadRef, StateValue
from scopecat.kernel.value_identity import scalar_values_equal
from scopecat.measurements.values import (
    MeasurementValueCandidate,
)
from scopecat.sdk.domain._identities import product_use_id
from scopecat.sdk.domain.batch import DomainBatchRequest
from scopecat.sdk.domain.execution import (
    DomainResidencyAddress,
    DomainResidencyRequirement,
    DomainStateAddress,
    DomainStateRequirement,
    DomainTransitionPolicy,
    ErasedDomainInvocation,
    ErasedDomainJobRuntime,
    ErasedDomainRealizer,
    ErasedDomainSetup,
    PreparedDomainExecution,
)
from scopecat.sdk.domain.invocation import (
    DomainOutputValue,
    close_domain_invocation,
    stream_domain_output_values,
)
from scopecat.sdk.domain.job import (
    DomainInvocationSpec,
    DomainResultValue,
)
from scopecat.sdk.domain.parameter_evidence import attach_domain_input_reads
from scopecat.sdk.domain.result_mapping import (
    DomainMappedResult,
    DomainResultBinding,
    DomainResultMapping,
)
from scopecat.sdk.domain.runtime import (
    DomainExecutionResult,
    DomainJobRuntime,
    DomainSetup,
)


class DomainPreparationBuilder:
    """Context-bound constructor for one complete prepared execution.

    Result mapping, value ownership, invocation identity, and result decoding
    are lowered behind this facade. Laboratory adapters provide only SDK
    references and target-owned payloads.
    """

    __slots__ = ("_context",)

    def __init__(self, context: DomainBatchRequest) -> None:
        self._context = context

    @property
    def context(self) -> DomainBatchRequest:
        return self._context

    def map_measurements[
        ResultAddressT: Hashable,
    ](
        self,
        *,
        results: Sequence[DomainResultBinding[ResultAddressT]],
    ) -> DomainResultMapping[ResultAddressT]:
        """Close exact direct-result coverage for the current selected batch."""

        selected_results = tuple(results)

        context = self._context
        point_ids = {id(point) for point in context.points}
        if any(id(binding.point) not in point_ids for binding in selected_results):
            msg = "domain result binding references a point outside this batch context"
            raise ValueError(msg)
        direct_use_ids = {id(product_use) for product_use in context.product_uses}
        if any(
            id(binding.product_use) not in direct_use_ids
            for binding in selected_results
        ):
            msg = "domain result binding references a foreign product use"
            raise ValueError(msg)

        return _close_result_mapping(
            context,
            selected_results,
        )

    def build[
        ResultAddressT: Hashable,
        PayloadT,
        ResultT,
    ](
        self,
        *,
        instrument_ids: Sequence[str],
        setup: DomainSetup[PayloadT] | None = None,
        setup_write_footprint: Sequence[DomainStateAddress] = (),
        setup_state_invalidations: Sequence[DomainStateAddress] = (),
        setup_residency_requirements: Sequence[DomainResidencyRequirement] = (),
        setup_residency_invalidations: Sequence[DomainResidencyAddress] = (),
        state_requirements: Sequence[DomainStateRequirement],
        realtime_write_footprint: Sequence[DomainStateAddress],
        realtime_state_invalidations: Sequence[DomainStateAddress],
        realtime_residency_invalidations: Sequence[DomainResidencyAddress] = (),
        transition_policy: DomainTransitionPolicy = "write_ahead",
        next_batch_max_points: int,
        inspection: CompiledArtifactInspection | None = None,
        inspection_projector: (
            Callable[
                [CompiledProgramInspectionQuery | None],
                CompiledArtifactInspection,
            ]
            | None
        ) = None,
        mapping: DomainResultMapping[ResultAddressT],
        invocation: DomainInvocationSpec[PayloadT],
        job_runtime: DomainJobRuntime[PayloadT, ResultT],
        realize: Callable[
            [DomainExecutionResult[ResultT]],
            Iterable[DomainResultValue[ResultAddressT]],
        ],
    ) -> PreparedDomainExecution:
        """Close one declarative target job behind the core execution ABI.

        Setup write footprints and invalidations occur before host-managed
        requirements are reconciled. ``realtime_write_footprint`` declares
        runtime authority with an unknown postcondition;
        ``realtime_state_invalidations`` withdraw knowledge about other
        physically coupled properties after the complete job.
        ``setup_residency_requirements`` identify opaque connection-owned
        content that successful setup makes resident. Matching content may
        skip setup later in the same run; explicit residency invalidations
        withdraw only that runtime knowledge.
        ``transition_policy`` keeps write-ahead persistence as the default;
        ``batched`` retains the complete ledger with bounded commit batches,
        while ``abnormal_only`` omits ordinary synchronous successes and retains
        only negative, interrupted, or checkpoint-bearing executions.
        ``next_batch_max_points`` bounds the next candidate using information
        learned from this concrete artifact. It may account for bytes, samples,
        shots, channels, device entries, or another domain-owned resource
        budget; ``prepare_batch`` still selects that candidate's compatible
        prefix after its point-local inputs are available.
        """

        if mapping.context is not self._context:
            msg = "domain result mapping belongs to another batch context"
            raise ValueError(msg)
        native_invocation = close_domain_invocation(
            mapping,
            invocation_id=invocation.invocation_id,
            target_id=invocation.target_id,
            compiler_id=invocation.compiler_id,
            capability_fingerprint=invocation.capability_fingerprint,
            artifact_id=invocation.artifact_id,
            artifact_fingerprint=invocation.artifact_fingerprint,
            execution_summary=invocation.execution_summary,
            target_intent=attach_domain_input_reads(
                self._context, invocation.target_intent
            ),
            payload=invocation.payload,
        )

        def close_realized_values(
            fetched: DomainExecutionResult[ResultT],
            accept: Callable[[MeasurementValueCandidate], None],
        ) -> None:
            stream_domain_output_values(
                mapping,
                (
                    DomainOutputValue(candidate.result_address, candidate.value)
                    for candidate in realize(fetched)
                ),
                accept=accept,
            )

        selected_instrument_ids = tuple(sorted(instrument_ids))
        selected_requirements = _select_state_requirements(state_requirements)
        selected_residency = _select_residency_requirements(
            setup_residency_requirements
        )
        return PreparedDomainExecution(
            instrument_ids=selected_instrument_ids,
            setup_write_footprint=tuple(sorted(set(setup_write_footprint))),
            setup_state_invalidations=tuple(sorted(set(setup_state_invalidations))),
            state_requirements=tuple(
                selected_requirements[address]
                for address in sorted(selected_requirements)
            ),
            realtime_write_footprint=tuple(sorted(set(realtime_write_footprint))),
            realtime_state_invalidations=tuple(
                sorted(set(realtime_state_invalidations))
            ),
            next_batch_max_points=next_batch_max_points,
            inspection=inspection,
            inspection_projector=inspection_projector,
            invocation=cast("ErasedDomainInvocation", native_invocation),
            setup=cast("ErasedDomainSetup | None", setup),
            job_runtime=cast("ErasedDomainJobRuntime", job_runtime),
            realize_into=cast("ErasedDomainRealizer", close_realized_values),
            setup_residency_requirements=tuple(
                selected_residency[address] for address in sorted(selected_residency)
            ),
            setup_residency_invalidations=tuple(
                sorted(set(setup_residency_invalidations))
            ),
            realtime_residency_invalidations=tuple(
                sorted(set(realtime_residency_invalidations))
            ),
            transition_policy=transition_policy,
        )


def _select_state_requirements(
    requirements: Sequence[DomainStateRequirement],
) -> dict[DomainStateAddress, DomainStateRequirement]:
    selected: dict[DomainStateAddress, DomainStateRequirement] = {}
    for requirement in requirements:
        previous = selected.get(requirement.address)
        if previous is None:
            selected[requirement.address] = requirement
            continue
        if not _state_values_equal(previous.value, requirement.value):
            address = requirement.address
            component = "/".join(address.component_path)
            mounted_interface = (
                f"{address.interface_id}/{component}"
                if component
                else address.interface_id
            )
            raise ValueError(
                "domain state requirements conflict for "
                f"{address.instrument_id}:{mounted_interface}."
                f"{address.property_id}"
            )
    return selected


def _select_residency_requirements(
    requirements: Sequence[DomainResidencyRequirement],
) -> dict[DomainResidencyAddress, DomainResidencyRequirement]:
    selected: dict[DomainResidencyAddress, DomainResidencyRequirement] = {}
    for requirement in requirements:
        previous = selected.get(requirement.address)
        if previous is None:
            selected[requirement.address] = requirement
            continue
        if previous.content_fingerprint != requirement.content_fingerprint:
            address = requirement.address
            raise ValueError(
                "domain residency requirements conflict for "
                f"{address.instrument_id}:{address.slot_id}"
            )
    return selected


def _state_values_equal(left: StateValue, right: StateValue) -> bool:
    left_value = left.root
    right_value = right.root
    if isinstance(left_value, PayloadRef) or isinstance(right_value, PayloadRef):
        return left_value == right_value
    return scalar_values_equal(left_value, right_value)


def _close_result_mapping[
    ResultAddressT: Hashable,
](
    context: DomainBatchRequest,
    result_bindings: tuple[DomainResultBinding[ResultAddressT], ...],
) -> DomainResultMapping[ResultAddressT]:
    use_refs = context.product_uses
    use_ref_by_id = {product_use_id(use): use for use in use_refs}
    use_order = {use_id: index for index, use_id in enumerate(use_ref_by_id)}
    point_order = {id(point): index for index, point in enumerate(context.points)}
    bindings_by_result: dict[
        ResultAddressT,
        list[DomainResultBinding[ResultAddressT]],
    ] = {}
    output_owners: dict[tuple[int, ProductUseId], ResultAddressT] = {}
    for binding in result_bindings:
        use_id = product_use_id(binding.product_use)
        if use_ref_by_id.get(use_id) is not binding.product_use:
            raise ValueError("domain result binding references a foreign product use")
        output = (id(binding.point), use_id)
        if output in output_owners:
            raise ValueError(
                "domain result bindings require unique point/product-use outputs"
            )
        output_owners[output] = binding.result_address
        bindings_by_result.setdefault(binding.result_address, []).append(binding)

    expected_outputs = {
        (id(point), use_id) for point in context.points for use_id in use_ref_by_id
    }
    if set(output_owners) != expected_outputs:
        raise ValueError(
            "domain result bindings must exactly cover every logical output"
        )

    catalog = context.measurement_catalog
    core_use_by_id = {use.id: use for use in catalog.product_uses}
    product_by_id = {product.id: product for product in catalog.product_defs}
    product_by_use_id = {
        use_id: product_by_id[core_use_by_id[use_id].product_id]
        for use_id in use_ref_by_id
    }
    address_by_logical_product: dict[
        tuple[int, ProductId],
        ResultAddressT,
    ] = {}
    for binding in result_bindings:
        use_id = product_use_id(binding.product_use)
        output = (id(binding.point), product_by_use_id[use_id].id)
        previous = address_by_logical_product.setdefault(
            output,
            binding.result_address,
        )
        if previous != binding.result_address:
            raise ValueError(
                "one logical product result cannot be split across locations"
            )
    mapped_results: list[DomainMappedResult[ResultAddressT]] = []
    for result_address, bindings in bindings_by_result.items():
        points = {id(binding.point): binding.point for binding in bindings}
        if len(points) != 1:
            raise ValueError(
                "one domain result location may supply only one logical point"
            )
        point = next(iter(points.values()))
        use_ids = tuple(
            use_id
            for use_id in use_ref_by_id
            if any(
                product_use_id(binding.product_use) == use_id for binding in bindings
            )
        )
        product_ids = {product_by_use_id[use_id].id for use_id in use_ids}
        if len(product_ids) != 1:
            raise ValueError(
                "one domain result may fan out only within one logical product"
            )
        mapped_results.append(
            DomainMappedResult(
                result_address=result_address,
                point=point,
                product_uses=tuple(use_ref_by_id[use_id] for use_id in use_ids),
                product=product_by_use_id[use_ids[0]],
            )
        )

    mapped_results.sort(
        key=lambda result: (
            point_order[id(result.point)],
            min(use_order[use_id] for use_id in result.product_use_ids),
        )
    )

    selected_results = tuple(mapped_results)
    selected_use_ids = tuple(use_ref_by_id)
    selected_core_uses = tuple(core_use_by_id[use_id] for use_id in selected_use_ids)
    fingerprint = stable_content_hash(
        content_fingerprint(
            {
                "schema": "scopecat.domain_result_contract.v5",
                "selected_product_uses": [
                    {
                        "product_use_id": use.id.value,
                        "product": product_by_use_id[use.id],
                    }
                    for use in selected_core_uses
                ],
                "results": [
                    {
                        "result_address": result.result_address,
                        "logical_point_id": result.logical_point_id.value,
                        "product_use_ids": [
                            use_id.value for use_id in result.product_use_ids
                        ],
                        "product": result.product,
                    }
                    for result in selected_results
                ],
            }
        )
    )
    return DomainResultMapping(
        context=context,
        selected_product_uses=selected_core_uses,
        results=selected_results,
        product_by_use_id=MappingProxyType(
            {use_id: product_by_use_id[use_id] for use_id in selected_use_ids}
        ),
        _contract_fingerprint=fingerprint,
    )
