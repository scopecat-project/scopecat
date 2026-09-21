"""Test-only in-process adapter for the daemon instrument-host port."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from scopecat.kernel.problems import Problem
from scopecat.planning.provider_binding import resolve_instrument_contract_catalog
from scopecat.planning.system import ExperimentSystem
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.content import CommandPayload
from scopecat.records.instrument import (
    InstrumentStateReadback,
    InstrumentStateSetting,
    InstrumentStateSnapshot,
    state_member_identity,
    state_member_ref,
)
from scopecat.records.measurement import InstrumentAcquisitionEvidence
from scopecat.sdk.domain.compiler import DomainCompiler
from scopecat.sdk.instruments import (
    DriverStateReadRequest,
    InstrumentBackend,
    InstrumentDescription,
    StateMemberRef,
)
from scopecat.sdk.instruments.backend import (
    BackendPayload,
    decode_driver_operation,
    lower_backend_apply_request,
    lower_backend_collect_request,
    lower_backend_invoke_request,
)
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat.sdk.instruments.commands import (
    CollectCommand,
    InstrumentStateCommand,
    InvokeCommand,
)
from scopecat.sdk.instruments.contracts import (
    capture_state_members,
    state_assignment_satisfied,
)
from scopecat.sdk.instruments.driver_adapter import (
    lower_acquisition,
    lower_state_patch,
    project_apply_outcome,
    project_collect_outcome,
    project_invoke_outcome,
    project_state_readback,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareApply,
    RunHardwareBatch,
    RunHardwareBatchReceipt,
    RunHardwareCollect,
    RunHardwareFinalizationReceipt,
    RunHardwareInvoke,
    RunHardwareStateActionReceipt,
    RunHardwareValue,
)
from scopecat.sdk.instruments.provider import (
    InstrumentConnectionContext,
    InstrumentDriver,
    InstrumentProvider,
    InstrumentProviderContext,
)
from scopecat.sdk.payloads import (
    EMPTY_PAYLOAD_CODECS,
    EncodedPayloadContent,
    PayloadCodecRegistry,
)
from scopecat.sdk.runtime_problems import runtime_problem


@dataclass(frozen=True, slots=True)
class TestInstrumentComposition:
    """Explicit planning and execution faces for an in-process test provider."""

    system: ExperimentSystem
    backend: InstrumentBackend


def compose_test_instruments(
    *,
    config: ConfigProfileSnapshot,
    provider: InstrumentProvider,
    domain_compiler: DomainCompiler | None = None,
    payload_codecs: PayloadCodecRegistry = EMPTY_PAYLOAD_CODECS,
) -> TestInstrumentComposition:
    backend = InstrumentBackend(
        provider=provider,
        driver_catalog=DriverCatalog(provider_id=provider.provider_id),
        payload_codecs=payload_codecs,
    )
    return TestInstrumentComposition(
        system=ExperimentSystem(
            instrument_catalog=resolve_instrument_contract_catalog(
                config=config,
                provider_id=provider.provider_id,
                describe=provider.describe,
            ),
            domain_compiler=domain_compiler,
            payload_codecs=payload_codecs,
        ),
        backend=backend,
    )


class TestRunInstrumentHost:
    """Adapt explicit fixture drivers without entering production execution code."""

    __test__ = False

    def __init__(
        self,
        drivers: Iterable[InstrumentDriver] = (),
        *,
        ready: bool = True,
        setup_problems: tuple[Problem, ...] = (),
        payload_codecs: PayloadCodecRegistry = EMPTY_PAYLOAD_CODECS,
    ) -> None:
        selected = tuple(drivers)
        self._drivers = {driver.instrument_id: driver for driver in selected}
        self._descriptions = {
            driver.instrument_id: driver.describe() for driver in selected
        }
        self._ready = ready
        self._setup_problems = setup_problems
        self._payload_codecs = payload_codecs
        self._observed_state = tuple(
            _capture_driver(driver, self._descriptions[driver.instrument_id])
            for driver in selected
        )
        self._baseline_state = self._observed_state
        self._assumed_states = {
            state.instrument_id: state for state in self._baseline_state
        }
        self._finished: RunHardwareFinalizationReceipt | None = None

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def setup_problems(self) -> tuple[Problem, ...]:
        return self._setup_problems

    @property
    def observed_state(self) -> tuple[InstrumentStateSnapshot, ...]:
        return self._observed_state

    @property
    def baseline_state(self) -> tuple[InstrumentStateSnapshot, ...]:
        return self._baseline_state

    def execute(self, batch: RunHardwareBatch) -> RunHardwareBatchReceipt:
        completed_effect_ids: list[str] = []
        values: list[RunHardwareValue] = []
        state_actions: list[RunHardwareStateActionReceipt] = []
        problems: list[Problem] = []
        indeterminate = False
        for action in batch.actions:
            driver = self._drivers[action.instrument_id]
            if isinstance(action, RunHardwareApply):
                current = self._assumed_states[action.instrument_id]
                assignments = [
                    assignment
                    for assignment in action.assignments
                    if not state_assignment_satisfied(current, assignment)
                ]
                if not assignments:
                    state_actions.append(
                        RunHardwareStateActionReceipt(
                            operation_id=action.effect_id,
                            instrument_id=action.instrument_id,
                            point_index=action.point_index,
                            status="unchanged",
                        )
                    )
                    completed_effect_ids.append(action.effect_id)
                    continue
                command = InstrumentStateCommand(
                    command_id=action.effect_id,
                    instrument_id=action.instrument_id,
                    assignments=assignments,
                )
                request = lower_backend_apply_request(command)
                receipt = project_apply_outcome(
                    driver.instrument_id,
                    driver.apply_state(lower_state_patch(request)),
                )
                problems.extend(receipt.problems)
                if receipt.status != "applied":
                    indeterminate = receipt.status == "unknown"
                    break
                readback = receipt.readback or _read_driver_members(
                    driver,
                    tuple(
                        state_member_ref(assignment.target)
                        for assignment in assignments
                    ),
                )
                observed = _merge_state(current, readback)
                if not all(
                    state_assignment_satisfied(observed, assignment)
                    for assignment in assignments
                ):
                    problems.append(
                        runtime_problem(
                            "instrument_apply_state_mismatch",
                            "instrument apply readback does not match requested state",
                            run_id="test-run",
                        )
                    )
                    indeterminate = True
                    break
                self._assumed_states[action.instrument_id] = observed
                state_actions.append(
                    RunHardwareStateActionReceipt(
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                        status="applied",
                        assignments=tuple(
                            InstrumentStateSetting(
                                target=assignment.target,
                                value=assignment.value,
                            )
                            for assignment in assignments
                        ),
                        metadata=dict(receipt.metadata),
                    )
                )
                completed_effect_ids.append(action.effect_id)
                continue
            if isinstance(action, RunHardwareInvoke):
                command = InvokeCommand(
                    command_id=action.effect_id,
                    instrument_id=action.instrument_id,
                    resource_id=action.resource_id,
                    interface_id=action.interface_id,
                    component_path=list(action.component_path),
                    operation_id=action.operation_id,
                    arguments=list(action.arguments),
                    payloads=action.payloads,
                    entity_ids=list(action.entity_ids),
                    channel_bindings=list(action.channel_bindings),
                )
                driver_request = decode_driver_operation(
                    lower_backend_invoke_request(
                        command,
                        materialized_payloads=_materialize_backend_payloads(
                            command.payloads
                        ),
                    ),
                    self._payload_codecs,
                )
                receipt = project_invoke_outcome(
                    driver.instrument_id,
                    driver.invoke(driver_request),
                )
                problems.extend(receipt.problems)
                if receipt.status != "invoked":
                    indeterminate = receipt.status == "unknown"
                    break
                self._assumed_states[action.instrument_id] = _capture_driver(
                    driver,
                    self._descriptions[action.instrument_id],
                )
                completed_effect_ids.append(action.effect_id)
                continue
            assert isinstance(action, RunHardwareCollect)
            command = CollectCommand(
                command_id=action.effect_id,
                instrument_id=action.instrument_id,
                point_index=action.point_index,
                point_count=action.point_count,
                requests=list(action.requests),
            )
            driver_request = lower_backend_collect_request(command)
            started_at = datetime.now(UTC)
            receipt = project_collect_outcome(
                driver_request,
                driver.collect(lower_acquisition(driver_request)),
            )
            completed_at = datetime.now(UTC)
            problems.extend(receipt.problems)
            if receipt.status != "collected" or receipt.readback is None:
                indeterminate = receipt.status == "unknown"
                break
            bindings = {
                binding.request_id: binding.value_ids for binding in action.bindings
            }
            requests = {request.id: request for request in action.requests}
            if set(receipt.readback.values) != set(bindings):
                problems.append(
                    runtime_problem(
                        "instrument_unexpected_product",
                        "instrument readback does not match requested results",
                        run_id="test-run",
                    )
                )
                break
            values.extend(
                RunHardwareValue(
                    point_index=action.point_index,
                    value_id=value_id,
                    value=value,
                    evidence=InstrumentAcquisitionEvidence(
                        command_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        interface_id=requests[request_id].interface_id,
                        component_path=tuple(requests[request_id].component_path),
                        acquisition_id=requests[request_id].acquisition_id,
                        result_id=requests[request_id].result_id,
                        started_at=started_at,
                        completed_at=completed_at,
                    ),
                )
                for request_id, value in receipt.readback.values.items()
                for value_id in bindings[request_id]
            )
            completed_effect_ids.append(action.effect_id)
        return RunHardwareBatchReceipt(
            operation_id=batch.operation_id,
            completed_effect_ids=tuple(completed_effect_ids),
            values=tuple(values),
            state_actions=tuple(state_actions),
            problems=tuple(problems),
            indeterminate=indeterminate,
        )

    def finish(
        self,
        *,
        operation_id: str,
        failed: bool,
    ) -> RunHardwareFinalizationReceipt:
        if self._finished is not None:
            return self._finished
        try:
            if failed:
                for driver in reversed(tuple(self._drivers.values())):
                    driver.abort()
            final_state = tuple(
                _capture_driver(driver, self._descriptions[driver.instrument_id])
                for driver in self._drivers.values()
            )
        finally:
            for driver in reversed(tuple(self._drivers.values())):
                driver.disconnect()
        self._finished = RunHardwareFinalizationReceipt(
            operation_id=operation_id,
            final_state=final_state,
        )
        return self._finished


def _read_driver_members(
    driver: InstrumentDriver,
    targets: Sequence[StateMemberRef],
) -> InstrumentStateReadback:
    return project_state_readback(
        driver.instrument_id,
        driver.read_state(DriverStateReadRequest(frozenset(targets))),
    )


def _capture_driver(
    driver: InstrumentDriver,
    description: InstrumentDescription,
) -> InstrumentStateSnapshot:
    selected = capture_state_members(description)
    readback = _read_driver_members(driver, selected)
    return InstrumentStateSnapshot(
        instrument_id=driver.instrument_id,
        observations=[item.model_copy(deep=True) for item in readback.observations],
    )


def _merge_state(
    state: InstrumentStateSnapshot,
    readback: InstrumentStateReadback,
) -> InstrumentStateSnapshot:
    observations = {
        state_member_identity(item.target): item.model_copy(deep=True)
        for item in state.observations
    }
    observations.update(
        {
            state_member_identity(item.target): item.model_copy(deep=True)
            for item in readback.observations
        }
    )
    return InstrumentStateSnapshot(
        instrument_id=state.instrument_id,
        observations=[observations[key] for key in sorted(observations, key=repr)],
    )


def _materialize_backend_payloads(
    payloads: Mapping[str, CommandPayload],
) -> dict[str, BackendPayload]:
    materialized: dict[str, BackendPayload] = {}
    for payload_id, payload in payloads.items():
        content = payload.inline_bytes()
        payload.verify_content(content)
        materialized[payload_id] = BackendPayload(
            id=payload.id,
            schema_id=payload.schema_id,
            codec_id=payload.codec_id,
            codec_version=payload.codec_version,
            media_type=payload.media_type,
            content_format=payload.content_format,
            content=EncodedPayloadContent.from_flat_bytes(
                content,
                payload.content_format,
            ),
        )
    return materialized


def provision_test_instrument_host(
    backend: InstrumentBackend | None,
    *,
    context: InstrumentProviderContext,
    instrument_ids: Sequence[str],
) -> TestRunInstrumentHost:
    """Provision fixture drivers outside the production Notebook executor."""

    if not instrument_ids:
        return TestRunInstrumentHost()
    if backend is None:
        raise ValueError("instrument claims require a test instrument backend")
    provider = backend.provider
    bindings = {binding.id: binding for binding in context.bindings}
    drivers: list[InstrumentDriver] = []
    try:
        for instrument_id in instrument_ids:
            drivers.append(
                provider.connect(
                    InstrumentConnectionContext(
                        binding=bindings[instrument_id],
                    )
                )
            )
    except Exception:
        for driver in reversed(drivers):
            driver.disconnect()
        raise
    return TestRunInstrumentHost(
        drivers,
        payload_codecs=backend.payload_codecs,
    )


__all__ = ["TestRunInstrumentHost", "provision_test_instrument_host"]
