"""Pure lowering, reconciliation, and cleanup helpers for instrument runtimes."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress

from scopecat.daemon.views import (
    DriverManagedInstrumentConnectionSummary,
    InstrumentConnectionSummary,
    SerialInstrumentConnectionSummary,
    TcpipSocketInstrumentConnectionSummary,
    VirtualInstrumentConnectionSummary,
)
from scopecat.kernel.problems import (
    ModelLocation,
    Problem,
    ProblemPhase,
    RuntimeLocation,
    problem,
)
from scopecat.records.config import (
    DriverManagedInstrumentConnection,
    InstrumentConnection,
    InstrumentSpec,
    SerialInstrumentConnection,
    VirtualInstrumentConnection,
)
from scopecat.records.content import CommandPayload
from scopecat.records.instrument import (
    InstrumentStateSetting,
    InstrumentStateSnapshot,
    state_member_identity,
)
from scopecat.sdk.instruments.backend import (
    BackendApplyRequest,
    BackendCollectRequest,
    BackendInvokeRequest,
    BackendPayload,
    lower_backend_apply_request,
    lower_backend_collect_request,
    lower_backend_invoke_request,
)
from scopecat.sdk.instruments.commands import (
    CollectCommand,
    InstrumentStateAssignment,
    InstrumentStateCommand,
    InvokeCommand,
)
from scopecat.sdk.instruments.contracts import (
    restorable_state_members,
    state_assignment_satisfied,
    validate_reconciled_state_assignments,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareApply,
    RunHardwareCollect,
    RunHardwareInvoke,
)
from scopecat.sdk.payloads import PayloadCodecCatalog

from ._runtime_state import OwnershipRuntime
from .actors import InstrumentActorConflict, OwnedInstrument
from .backend import InstrumentBackendEndpoint

type _BackendHardwareRequest = (
    BackendApplyRequest | BackendInvokeRequest | BackendCollectRequest
)


def instrument_connection_summary(
    connection: InstrumentConnection,
) -> InstrumentConnectionSummary:
    if isinstance(connection, DriverManagedInstrumentConnection):
        return DriverManagedInstrumentConnectionSummary()
    if isinstance(connection, VirtualInstrumentConnection):
        return VirtualInstrumentConnectionSummary()
    if isinstance(connection, SerialInstrumentConnection):
        return SerialInstrumentConnectionSummary(
            port=connection.port,
            baud_rate=connection.baud_rate,
        )
    return TcpipSocketInstrumentConnectionSummary(
        host=connection.host,
        port=connection.port,
    )


class DefaultStateReconciliationRejected(RuntimeError):
    def __init__(
        self,
        *,
        problems: tuple[Problem, ...],
    ) -> None:
        self.problems = problems
        super().__init__("instrument default-state reconciliation was rejected")


class DefaultStateReconciliationUnknown(RuntimeError):
    pass


class HardwareActionRejected(RuntimeError):
    def __init__(self, problems: Sequence[Problem]) -> None:
        self.problems = tuple(problems)
        super().__init__("; ".join(item.message for item in self.problems))


class HardwareActionIndeterminate(RuntimeError):
    def __init__(self, problems: Sequence[Problem], *, reason: str) -> None:
        self.problems = tuple(problems)
        self.reason = reason
        super().__init__("; ".join(item.message for item in self.problems))


def provision_problem(
    code: str,
    message: str,
    *,
    run_id: str | None = None,
    operation_id: str | None = None,
    instrument_id: str | None = None,
    details: Mapping[str, object] | None = None,
) -> Problem:
    location = (
        RuntimeLocation(
            run_id=run_id,
            operation_id=operation_id,
            instrument_id=instrument_id,
        )
        if run_id is not None or operation_id is not None
        else ModelLocation(
            root="instrument_provider",
            path=(() if instrument_id is None else ("instruments", instrument_id)),
        )
    )
    return problem(
        code,
        message,
        phase=ProblemPhase.PROVIDER_PREFLIGHT,
        location=location,
        details=details,
    )


def lower_hardware_action(
    action: RunHardwareApply | RunHardwareInvoke | RunHardwareCollect,
    *,
    materialized_payloads: Mapping[str, BackendPayload],
) -> _BackendHardwareRequest:
    if isinstance(action, RunHardwareApply):
        return lower_backend_apply_request(
            InstrumentStateCommand(
                command_id=action.effect_id,
                instrument_id=action.instrument_id,
                assignments=list(action.assignments),
            )
        )
    if isinstance(action, RunHardwareInvoke):
        return lower_backend_invoke_request(
            InvokeCommand(
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
            ),
            materialized_payloads=materialized_payloads,
        )
    return lower_backend_collect_request(
        CollectCommand(
            command_id=action.effect_id,
            instrument_id=action.instrument_id,
            point_index=action.point_index,
            point_count=action.point_count,
            requests=list(action.requests),
        )
    )


def hardware_problem(
    code: str,
    message: str,
    *,
    run_id: str,
    operation_id: str,
    instrument_id: str | None = None,
    point_index: int | None = None,
) -> Problem:
    return problem(
        code,
        message,
        phase=ProblemPhase.EXECUTION,
        location=RuntimeLocation(
            run_id=run_id,
            operation_id=operation_id,
            instrument_id=instrument_id,
            point_index=point_index,
        ),
    )


def payload_codec_issues(
    payloads: Mapping[str, CommandPayload],
    catalog: PayloadCodecCatalog,
) -> tuple[tuple[str, str], ...]:
    issues: list[tuple[str, str]] = []
    for payload_id, payload in payloads.items():
        try:
            catalog.validate_descriptor(payload)
        except LookupError as error:
            issues.append(
                (
                    "instrument_payload_codec_unavailable",
                    f"payload {payload_id!r}: {error}",
                )
            )
        except ValueError as error:
            issues.append(
                (
                    "instrument_payload_codec_mismatch",
                    f"payload {payload_id!r}: {error}",
                )
            )
    return tuple(issues)


def payload_codec_problems(
    payloads: Mapping[str, CommandPayload],
    catalog: PayloadCodecCatalog,
    *,
    run_id: str,
    operation_id: str,
    instrument_id: str,
    point_index: int | None,
) -> tuple[Problem, ...]:
    return tuple(
        hardware_problem(
            code,
            message,
            run_id=run_id,
            operation_id=operation_id,
            instrument_id=instrument_id,
            point_index=point_index,
        )
        for code, message in payload_codec_issues(payloads, catalog)
    )


def configured_state_assignments(
    *,
    instrument_id: str,
    configured_state: Sequence[InstrumentStateSetting],
    instrument: OwnedInstrument,
) -> tuple[InstrumentStateAssignment, ...]:
    assignments = tuple(
        InstrumentStateAssignment(
            resource_id=instrument_id,
            target=item.target,
            value=item.value,
        )
        for item in configured_state
    )
    problems = validate_reconciled_state_assignments(
        instrument_id=instrument_id,
        assignments=assignments,
        description=instrument.description,
    )
    if problems:
        raise DefaultStateReconciliationRejected(
            problems=tuple(problems),
        )
    return assignments


def restorable_state_assignments(
    *,
    instrument_id: str,
    baseline_state: InstrumentStateSnapshot,
    instrument: OwnedInstrument,
) -> tuple[InstrumentStateAssignment, ...]:
    restorable = {
        state_member_identity(target)
        for target in restorable_state_members(instrument.description)
    }
    return tuple(
        InstrumentStateAssignment(
            resource_id=instrument_id,
            target=item.target,
            value=item.value,
        )
        for item in baseline_state.observations
        if state_member_identity(item.target) in restorable
    )


def pending_configured_state_command(
    *,
    instrument_id: str,
    assignments: Sequence[InstrumentStateAssignment],
    instrument: OwnedInstrument,
    observed_state: InstrumentStateSnapshot,
    operation_id: str,
) -> InstrumentStateCommand | None:
    pending = [
        assignment
        for assignment in assignments
        if not state_assignment_satisfied(observed_state, assignment)
    ]
    if not pending:
        return None
    problems = validate_reconciled_state_assignments(
        instrument_id=instrument_id,
        assignments=pending,
        description=instrument.description,
        baseline=observed_state,
    )
    if problems:
        raise DefaultStateReconciliationRejected(
            problems=tuple(problems),
        )
    return InstrumentStateCommand(
        command_id=operation_id,
        instrument_id=instrument_id,
        assignments=pending,
    )


def shutdown_endpoint(endpoint: InstrumentBackendEndpoint) -> None:
    with suppress(Exception):
        endpoint.shutdown()


def release_instruments(instruments: Iterable[OwnedInstrument]) -> bool:
    failed = False
    for instrument in reversed(tuple(instruments)):
        try:
            instrument.release()
        except Exception:
            failed = True
    return failed


def abort_instruments(
    instruments: Iterable[OwnedInstrument],
    *,
    failures: list[tuple[OwnedInstrument, str, Exception]] | None = None,
) -> bool:
    failed = False
    for instrument in reversed(tuple(instruments)):
        try:
            instrument.abort()
        except Exception as error:
            if failures is not None:
                failures.append((instrument, "abort", error))
            failed = True
    return failed


def fault_ownership(
    runtime: OwnershipRuntime,
    *,
    abort: bool,
    failures: list[tuple[OwnedInstrument, str, Exception]] | None = None,
) -> bool:
    failed = (
        abort_instruments(runtime.instruments.values(), failures=failures)
        if abort
        else False
    )
    for instrument in reversed(tuple(runtime.instruments.values())):
        try:
            instrument.fault()
        except InstrumentActorConflict:
            continue
        except Exception as error:
            if failures is not None:
                failures.append((instrument, "disconnect", error))
            failed = True
    return failed


def scope_provider_problems(
    specs: list[InstrumentSpec],
    problems: tuple[Problem, ...],
) -> tuple[tuple[Problem, ...], dict[str, tuple[Problem, ...]]]:
    instrument_ids = {spec.id for spec in specs}
    scoped: dict[str, list[Problem]] = {}
    global_problems: list[Problem] = []
    for item in problems:
        owners = problem_instrument_ids(
            item,
            specs=specs,
            instrument_ids=instrument_ids,
        )
        if not owners:
            global_problems.append(item)
            continue
        for instrument_id in owners:
            scoped.setdefault(instrument_id, []).append(item)
    return (
        tuple(global_problems),
        {instrument_id: tuple(items) for instrument_id, items in scoped.items()},
    )


def problem_instrument_ids(
    problem: Problem,
    *,
    specs: list[InstrumentSpec],
    instrument_ids: set[str],
) -> tuple[str, ...]:
    selected: set[str] = set()
    detail_id = problem.details.get("instrument_id")
    if isinstance(detail_id, str) and detail_id in instrument_ids:
        selected.add(detail_id)
    for location in (
        *((problem.location,) if problem.location is not None else ()),
        *problem.related_locations,
    ):
        if (
            isinstance(location, RuntimeLocation)
            and location.instrument_id in instrument_ids
        ):
            assert location.instrument_id is not None
            selected.add(location.instrument_id)
        elif isinstance(location, ModelLocation):
            selected.update(
                item
                for item in location.path
                if isinstance(item, str) and item in instrument_ids
            )
            for index, item in enumerate(location.path[:-1]):
                candidate = location.path[index + 1]
                if (
                    item == "instruments"
                    and isinstance(candidate, int)
                    and candidate < len(specs)
                ):
                    selected.add(specs[candidate].id)
    return tuple(sorted(selected))
