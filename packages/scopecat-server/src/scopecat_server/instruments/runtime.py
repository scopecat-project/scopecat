"""Daemon-owned instruments for direct sessions and admitted experiment runs."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from threading import Lock, RLock, Timer
from time import perf_counter
from typing import TYPE_CHECKING, Literal, cast

from pydantic import JsonValue
from scopecat.control.models import (
    DurableEventInput,
    InstrumentSession,
    ResourceClaim,
    ResourceKey,
)
from scopecat.daemon.views import InstrumentListView, InstrumentView
from scopecat.daemon.wire import (
    InstrumentConfiguredDefaultsApplyCommand,
    InstrumentDriverProbeCommand,
    InstrumentDriverProbeReceipt,
    InstrumentReleaseCommand,
    InstrumentReleaseReceipt,
    InstrumentSessionEndReceipt,
    InstrumentSessionLeaseReceipt,
    InstrumentSessionOpenCommand,
    InstrumentSessionOpenReceipt,
    RunHardwareBatchCommand,
    RunHardwareFinishCommand,
    RunInstrumentProvisionCommand,
    RunInstrumentProvisionReceipt,
)
from scopecat.kernel.content_identity import (
    model_wire_content_hash,
    stable_content_hash,
)
from scopecat.kernel.problems import Problem
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.provider_binding import resolve_instrument_contract_catalog
from scopecat.planning.provider_validation import (
    instrument_contract_fingerprint,
)
from scopecat.records.config import (
    ConfigProfileSnapshot,
    InstrumentBindingSpec,
    InstrumentSafeOperation,
    InstrumentSpec,
    config_content_hash,
    instrument_bindings,
)
from scopecat.records.costs import RunFinalizationCost, RunOperationCost
from scopecat.records.instrument import (
    InstrumentStateCacheReadback,
    InstrumentStateReadback,
    InstrumentStateSetting,
    InstrumentStateSnapshot,
    state_member_ref,
)
from scopecat.records.measurement import InstrumentAcquisitionEvidence
from scopecat.sdk.instruments.backend import (
    BackendAcquisitionPlan,
    BackendApplyRequest,
    BackendCollectRequest,
    BackendInvokeRequest,
    lower_backend_apply_request,
    lower_backend_collect_request,
    lower_backend_invoke_request,
)
from scopecat.sdk.instruments.catalog import DriverCatalog
from scopecat.sdk.instruments.commands import (
    ApplyReceipt,
    CollectCommand,
    CollectReceipt,
    InstrumentConfiguredDefaultsApplyReceipt,
    InstrumentOperationArgument,
    InstrumentStateCommand,
    InstrumentStateReadCommand,
    InteractiveCollectIntent,
    InvokeCommand,
    InvokeReceipt,
    RejectedInteractiveCollect,
)
from scopecat.sdk.instruments.contracts import (
    InstrumentDescription,
    project_instrument_invoke_state,
    project_instrument_state,
    resolve_interactive_collect,
    state_assignment_satisfied,
    validate_collect_command,
    validate_collect_plan,
    validate_invoke_command,
    validate_state_command,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareAction,
    RunHardwareApply,
    RunHardwareBatchReceipt,
    RunHardwareCollect,
    RunHardwareFinalizationActionReceipt,
    RunHardwareFinalizationReceipt,
    RunHardwareInvoke,
    RunHardwareStateActionReceipt,
    RunHardwareValue,
)
from scopecat.sdk.instruments.projection import ProjectedInstrumentState
from scopecat.sdk.payloads import PayloadCodecCatalog
from scopecat.sdk.runtime_problems import contextualize_problems

from scopecat_server.storage.sqlite.control_plane import (
    ControlPlaneConflict,
    ControlPlaneNotFound,
    ExecutorLeaseNotHeld,
    InstrumentSessionNotActive,
    SQLiteControlPlane,
)
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository

from ..command_payloads import (
    CommandPayloadError,
    run_payload_scope,
    session_payload_scope,
)
from ..errors import BackendConflict, BackendNotFound
from ._runtime_state import (
    ApplyReplay,
    CollectFailureReplay,
    CollectReceiptReplay,
    CollectRejectionReplay,
    ConfiguredDefaultsReplay,
    InstrumentOperationLedger,
    InvokeReplay,
    OwnershipRuntime,
    RunContext,
    RunFinalized,
    RunFinalizing,
    RunProvision,
    SessionContext,
)
from ._runtime_support import (
    DefaultStateReconciliationRejected,
    DefaultStateReconciliationUnknown,
    HardwareActionIndeterminate,
    HardwareActionRejected,
    abort_instruments,
    configured_state_assignments,
    fault_ownership,
    hardware_problem,
    instrument_connection_summary,
    lower_hardware_action,
    payload_codec_issues,
    payload_codec_problems,
    pending_configured_state_command,
    provision_problem,
    release_instruments,
    restorable_state_assignments,
    scope_provider_problems,
    shutdown_endpoint,
)
from .actors import (
    InstrumentActorConflict,
    InstrumentActorRegistry,
    InstrumentActorShutdown,
    InstrumentBindingKey,
    InstrumentOwnerKey,
    OwnedInstrument,
)
from .backend import (
    InstrumentBackendEndpoint,
    InstrumentBackendError,
    InstrumentBackendRejected,
    InstrumentBackendUnavailable,
)
from .commands import (
    InstrumentCommandExecutionError,
    execute_instrument_acquisition_prepare,
    execute_instrument_apply,
    execute_instrument_collect,
    execute_instrument_invoke,
    observe_instrument,
    observe_members,
    observed_members,
)
from .costs import observe_operation

if TYPE_CHECKING:
    from ..command_payloads import CommandPayloadScope, CommandPayloadService
    from ..services.config import ConfigService


class InstrumentRuntime:
    """Coordinate durable claims with process-long instrument connections."""

    def __init__(
        self,
        *,
        control: SQLiteControlPlane,
        runs: SQLiteRunRepository,
        config: ConfigService,
        endpoint: InstrumentBackendEndpoint | None,
        payloads: CommandPayloadService,
        actors: InstrumentActorRegistry,
        shutdown_grace_seconds: float,
        session_lease_ttl: timedelta,
    ) -> None:
        if session_lease_ttl.total_seconds() <= 0:
            raise ValueError("instrument session lease TTL must be positive")
        self._control = control
        self._runs = runs
        self._config = config
        self._endpoint = endpoint
        self._payloads = payloads
        self._actors = actors
        self._shutdown_grace_seconds = shutdown_grace_seconds
        self._session_lease_ttl = session_lease_ttl
        self._sessions: dict[str, SessionContext] = {}
        self._run_contexts: dict[str, RunContext] = {}
        self._sessions_lock = RLock()
        self._open_lock = RLock()
        self._run_lock = RLock()
        self._lifecycle_lock = RLock()
        self._shutdown_lock = Lock()
        self._stopping = False
        self._attention_lock = RLock()

    @property
    def healthy(self) -> bool:
        endpoint = self._endpoint
        return endpoint is None or endpoint.healthy

    def release_idle_instruments(
        self, command: InstrumentReleaseCommand
    ) -> InstrumentReleaseReceipt:
        """Retire connections behind an acquisition gate without stopping workers."""
        self._require_running()
        registry = self._config.get_active_config().config.instrument_registry
        specs = {item.id: item for item in registry.instruments}
        for instrument_id in command.instrument_ids:
            if instrument_id not in specs:
                raise BackendNotFound(f"instrument was not found: {instrument_id}")
        keys = tuple(specs[item].exclusivity_key for item in command.instrument_ids)
        try:
            with self._actors.begin_retirement(keys) as retirement:
                with self._control.read_transaction() as connection:
                    blockers = (
                        self._control.inventory_migration_blockers_in_transaction(
                            connection,
                            tuple(ResourceKey.instrument(key) for key in keys),
                        )
                    )
                if blockers:
                    raise BackendConflict("instrument release requires idle devices")
                retirement.retire_idle()
        except (InstrumentActorConflict, InstrumentActorShutdown) as error:
            raise BackendConflict(str(error)) from error
        return InstrumentReleaseReceipt(instrument_ids=command.instrument_ids)

    def list_instruments(self) -> InstrumentListView:
        active = self._config.get_active_config()
        catalog = self.resolve_instrument_contracts(active.config)
        descriptions = {
            description.instrument_id: description
            for description in catalog.instruments
        }
        global_problems, instrument_problems = scope_provider_problems(
            active.config.instrument_registry.instruments,
            catalog.problems,
        )
        with self._control.read_transaction() as connection:
            claims = {
                claim.resource.id: claim
                for claim in self._control.list_resource_claims_in_transaction(
                    connection
                )
                if claim.resource.kind == "instrument"
            }
        session_actors = {
            session.session_id: session.actor
            for session in self._control.list_instrument_sessions()
            if session.state != "closed"
        }
        items = tuple(
            self._instrument_view(
                spec,
                description=descriptions.get(spec.id),
                claim=claims.get(spec.exclusivity_key),
                owner_actor=(
                    session_actors.get(claim.owner_id)
                    if (
                        (claim := claims.get(spec.exclusivity_key)) is not None
                        and claim.owner_kind == "instrument_session"
                    )
                    else None
                ),
                problems=instrument_problems.get(spec.id, ()),
            )
            for spec in active.config.instrument_registry.instruments
        )
        return InstrumentListView(
            config_entry_id=active.entry.id,
            items=items,
            problems=global_problems,
        )

    def resolve_instrument_contracts(
        self,
        config: ConfigProfileSnapshot,
    ) -> InstrumentContractCatalog:
        """Resolve contracts against exactly the supplied config snapshot."""

        endpoint = self._endpoint
        if endpoint is None:
            return InstrumentContractCatalog(
                config_content_hash=config_content_hash(config),
                provider_id=None,
            )
        return resolve_instrument_contract_catalog(
            config=config,
            provider_id=endpoint.provider_id,
            describe=lambda context: endpoint.describe(context.bindings),
        )

    def driver_catalog(self) -> DriverCatalog:
        endpoint = self._endpoint
        if endpoint is None:
            raise BackendConflict("project does not configure an instrument backend")
        return endpoint.driver_catalog

    def probe_driver(
        self,
        command: InstrumentDriverProbeCommand,
    ) -> InstrumentDriverProbeReceipt:
        endpoint = self._endpoint
        if endpoint is None:
            raise BackendConflict("project does not configure an instrument backend")
        self._require_supported_binding(endpoint, command.binding)
        try:
            description = endpoint.probe(command.binding)
        except InstrumentBackendRejected as error:
            return InstrumentDriverProbeReceipt(
                status="rejected",
                problems=error.problems,
            )
        except InstrumentBackendUnavailable as error:
            raise BackendConflict(str(error)) from error
        return InstrumentDriverProbeReceipt(
            status="connected",
            description=description,
        )

    def get_instrument(self, instrument_id: str) -> InstrumentView:
        instruments = self.list_instruments()
        for item in instruments.items:
            if item.instrument_id == instrument_id:
                return item
        raise BackendNotFound(f"instrument was not found: {instrument_id}")

    def authorize_session_payload_upload(self, session_id: str) -> None:
        """Require an active daemon-owned session before accepting bytes."""

        try:
            self._control.validate_instrument_session(session_id)
            self._live_runtime(session_id)
        except ControlPlaneNotFound as error:
            raise BackendNotFound(str(error)) from error
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error

    def authorize_run_payload_upload(self, run_id: str, lease_id: str) -> None:
        """Fence a run-scoped upload with the current executor lease."""

        context = self._run_context_state(run_id)
        if context is None:
            raise BackendConflict("run instruments are not provisioned")
        with context.lock:
            self._fence_run(run_id, lease_id)

    def provision_run(
        self,
        run_id: str,
        command: RunInstrumentProvisionCommand,
    ) -> RunInstrumentProvisionReceipt:
        """Connect the exact instrument claims admitted with one fenced run."""

        self._require_running()
        # Validate before allocating volatile state, then again after waiting for
        # the per-run lock in case the executor lease changed meanwhile.
        self._fence_run(run_id, command.lease_id)
        context = self._create_run_context(run_id)
        with context.lock:
            try:
                self._require_running()
                return self._provision_run(run_id, command, context)
            except Exception:
                with self._run_lock:
                    if (
                        self._run_contexts.get(run_id) is context
                        and context.provision is None
                        and context.runtime is None
                        and context.finalization is None
                    ):
                        self._run_contexts.pop(run_id)
                raise

    def _provision_run(
        self,
        run_id: str,
        command: RunInstrumentProvisionCommand,
        context: RunContext,
    ) -> RunInstrumentProvisionReceipt:
        self._fence_run(run_id, command.lease_id)
        self._require_provisionable_run(context)
        cached = context.provision
        if cached is not None:
            if cached.command != command:
                if cached.command.operation_id == command.operation_id:
                    raise BackendConflict(
                        "run instrument provision operation id has different content"
                    )
                raise BackendConflict("run instruments are already provisioned")
            return cached.receipt

        try:
            control_run = self._control.get_run(run_id)
        except ControlPlaneNotFound as error:
            raise BackendNotFound(str(error)) from error
        instrument_claims = frozenset(
            claim.id
            for claim in control_run.admission.resource_claims
            if claim.kind == "instrument"
        )
        instrument_ids = control_run.admission.plan.host_instrument_order
        if not instrument_ids:
            receipt = RunInstrumentProvisionReceipt(
                run_id=run_id,
                operation_id=command.operation_id,
                status="ready",
            )
            self._store_run_provision(
                run_id,
                context,
                RunProvision(
                    command=command,
                    receipt=receipt,
                ),
            )
            return receipt

        config = self._runs.read_config_profile_snapshot(run_id)
        endpoint = self._endpoint
        if endpoint is None:
            return self._reject_run_provision(
                run_id,
                command,
                context=context,
                problems=(
                    provision_problem(
                        "instrument_provider_unavailable",
                        "project does not configure an instrument backend",
                        run_id=run_id,
                        operation_id=command.operation_id,
                    ),
                ),
            )
        catalog = self.resolve_instrument_contracts(config)
        provider_id = catalog.provider_id
        if provider_id is None:
            return self._reject_run_provision(
                run_id,
                command,
                context=context,
                problems=(
                    provision_problem(
                        "instrument_provider_unavailable",
                        "project instrument backend has no provider identity",
                        run_id=run_id,
                        operation_id=command.operation_id,
                    ),
                ),
            )
        global_problems, instrument_problems = scope_provider_problems(
            config.instrument_registry.instruments,
            catalog.problems,
        )
        setup_problems = list(global_problems)
        specs = {spec.id: spec for spec in config.instrument_registry.instruments}
        current_exclusivity_keys = tuple(
            spec.exclusivity_key
            for instrument_id in instrument_ids
            if (spec := specs.get(instrument_id)) is not None
        )
        if len(current_exclusivity_keys) != len(instrument_ids) or not set(
            current_exclusivity_keys
        ).issubset(instrument_claims):
            setup_problems.append(
                provision_problem(
                    "instrument_exclusivity_changed_after_admission",
                    "instrument exclusivity differs from the admitted resource claims",
                    run_id=run_id,
                    operation_id=command.operation_id,
                )
            )
        setup_problems.extend(
            item
            for instrument_id in instrument_ids
            for item in instrument_problems.get(instrument_id, ())
        )
        advertised = {
            description.instrument_id: description
            for description in catalog.instruments
            if description.instrument_id in instrument_ids
        }
        missing = tuple(
            instrument_id
            for instrument_id in instrument_ids
            if instrument_id not in advertised
        )
        setup_problems.extend(
            provision_problem(
                "instrument_claim_not_described",
                f"instrument provider does not describe admitted claim {instrument_id}",
                run_id=run_id,
                operation_id=command.operation_id,
                instrument_id=instrument_id,
            )
            for instrument_id in missing
        )
        if provider_id != control_run.admission.plan.host_provider_id:
            setup_problems.append(
                provision_problem(
                    "instrument_provider_changed_after_admission",
                    "instrument provider differs from the admitted contract",
                    run_id=run_id,
                    operation_id=command.operation_id,
                )
            )
        if not missing:
            descriptions = tuple(advertised[item] for item in instrument_ids)
            if (
                instrument_contract_fingerprint(provider_id, descriptions)
                != control_run.admission.plan.host_contract_fingerprint
            ):
                setup_problems.append(
                    provision_problem(
                        "instrument_contract_changed_after_admission",
                        "instrument descriptions differ from the admitted contract",
                        run_id=run_id,
                        operation_id=command.operation_id,
                    )
                )
        if setup_problems:
            return self._reject_run_provision(
                run_id,
                command,
                context=context,
                problems=tuple(setup_problems),
            )

        try:
            bindings = {binding.id: binding for binding in instrument_bindings(config)}
            specs = {spec.id: spec for spec in config.instrument_registry.instruments}
            runtime, observed_state = self._open_ownership(
                endpoint=endpoint,
                bindings=bindings,
                specs=specs,
                owner=InstrumentOwnerKey(
                    kind="run",
                    owner_id=run_id,
                    fence=command.lease_id,
                ),
                instrument_ids=instrument_ids,
                expected=advertised,
                payload_catalog=endpoint.payload_catalog,
            )
        except InstrumentBackendRejected as error:
            return self._reject_run_provision(
                run_id,
                command,
                context=context,
                problems=error.problems,
            )
        except InstrumentBackendUnavailable:
            return self._reject_run_provision(
                run_id,
                command,
                context=context,
                problems=(
                    provision_problem(
                        "instrument_connection_failed",
                        "instrument connection could not be established",
                        run_id=run_id,
                        operation_id=command.operation_id,
                    ),
                ),
            )
        except BackendConflict:
            return self._reject_run_provision(
                run_id,
                command,
                context=context,
                problems=(
                    provision_problem(
                        "instrument_observation_failed",
                        "instrument state could not be observed",
                        run_id=run_id,
                        operation_id=command.operation_id,
                    ),
                ),
            )
        run_start_state = self._reconcile_run_start_or_reject(
            run_id=run_id,
            command=command,
            context=context,
            config=config,
            instrument_ids=instrument_ids,
            runtime=runtime,
            observed_state=observed_state,
        )
        if isinstance(run_start_state, RunInstrumentProvisionReceipt):
            return run_start_state
        baseline_state = run_start_state
        for state in baseline_state:
            runtime.instruments[state.instrument_id].adopt_state(state)

        try:
            self._record_run_operation_event(
                run_id,
                token=command.lease_id,
                instrument_id=None,
                operation_id=command.operation_id,
                event_kind="run_instruments_provisioned",
                status="ready",
            )
        except BackendConflict:
            self._emergency_fault_ownership(
                runtime,
                operation_id="hardware.provision.owner_loss",
            )
            self._mark_run_unknown(
                run_id,
                token=command.lease_id,
                reason="run_instrument_provisioning_fence_lost",
            )
            raise

        receipt = RunInstrumentProvisionReceipt(
            run_id=run_id,
            operation_id=command.operation_id,
            status="ready",
            instrument_ids=instrument_ids,
            observed_state=observed_state,
            baseline_state=baseline_state,
        )
        provision = RunProvision(
            command=command,
            receipt=receipt,
        )
        self._store_run_provision(
            run_id,
            context,
            provision,
            runtime=runtime,
        )
        return receipt

    @staticmethod
    def _require_provisionable_run(context: RunContext) -> None:
        finalization = context.finalization
        if finalization is None:
            return
        if isinstance(finalization, RunFinalized):
            raise BackendConflict("run hardware is already finalized")
        raise BackendConflict("run instrument host is finalizing")

    def _reject_run_provision(
        self,
        run_id: str,
        command: RunInstrumentProvisionCommand,
        *,
        context: RunContext,
        problems: tuple[Problem, ...],
    ) -> RunInstrumentProvisionReceipt:
        receipt = RunInstrumentProvisionReceipt(
            run_id=run_id,
            operation_id=command.operation_id,
            status="rejected",
            problems=problems,
        )
        self._store_run_provision(
            run_id,
            context,
            RunProvision(
                command=command,
                receipt=receipt,
            ),
        )
        return receipt

    def _open_ownership(
        self,
        *,
        endpoint: InstrumentBackendEndpoint,
        bindings: Mapping[str, InstrumentBindingSpec],
        specs: Mapping[str, InstrumentSpec],
        owner: InstrumentOwnerKey,
        instrument_ids: tuple[str, ...],
        expected: Mapping[str, InstrumentDescription],
        payload_catalog: PayloadCodecCatalog,
    ) -> tuple[OwnershipRuntime, tuple[InstrumentStateSnapshot, ...]]:
        for attempt in range(2):
            runtime = self._acquire_ownership(
                endpoint=endpoint,
                bindings=bindings,
                specs=specs,
                owner=owner,
                instrument_ids=instrument_ids,
                expected=expected,
                payload_catalog=payload_catalog,
            )
            try:
                observed = tuple(
                    observe_instrument(runtime.instruments[instrument_id])
                    for instrument_id in instrument_ids
                )
            except BackendConflict:
                retry_stale_connection = attempt == 0 and any(
                    instrument.reused_connection
                    for instrument in runtime.instruments.values()
                )
                fault_ownership(runtime, abort=False)
                if retry_stale_connection:
                    continue
                raise
            for state in observed:
                runtime.instruments[state.instrument_id].adopt_state(state)
            runtime.opening_state = tuple(
                state.model_copy(deep=True) for state in observed
            )
            return runtime, observed
        raise AssertionError("instrument observation retry must return or raise")

    def _acquire_ownership(
        self,
        *,
        endpoint: InstrumentBackendEndpoint,
        bindings: Mapping[str, InstrumentBindingSpec],
        specs: Mapping[str, InstrumentSpec],
        owner: InstrumentOwnerKey,
        instrument_ids: tuple[str, ...],
        expected: Mapping[str, InstrumentDescription],
        payload_catalog: PayloadCodecCatalog,
    ) -> OwnershipRuntime:
        instruments: dict[str, OwnedInstrument] = {}
        try:
            for instrument_id in instrument_ids:
                instruments[instrument_id] = self._actors.acquire(
                    specs[instrument_id].exclusivity_key,
                    instrument_id,
                    binding=InstrumentBindingKey(
                        provider_id=endpoint.provider_id,
                        binding_fingerprint=model_wire_content_hash(
                            bindings[instrument_id]
                        ),
                        contract_fingerprint=model_wire_content_hash(
                            expected[instrument_id]
                        ),
                    ),
                    owner=owner,
                    endpoint=endpoint,
                    connect=lambda instrument_id=instrument_id: endpoint.connect(
                        binding=bindings[instrument_id],
                        expected=expected[instrument_id],
                    ),
                )
        except InstrumentBackendRejected:
            release_instruments(instruments.values())
            raise
        except InstrumentBackendUnavailable:
            release_instruments(instruments.values())
            raise
        except Exception as error:
            release_instruments(instruments.values())
            raise InstrumentBackendUnavailable(
                "instrument connection could not be established"
            ) from error
        return OwnershipRuntime(
            instruments=instruments,
            bindings={
                instrument_id: bindings[instrument_id].model_copy(deep=True)
                for instrument_id in instruments
            },
            specs={
                instrument_id: specs[instrument_id].model_copy(deep=True)
                for instrument_id in instruments
            },
            payload_catalog=payload_catalog,
            ledgers={
                instrument_id: InstrumentOperationLedger()
                for instrument_id in instruments
            },
        )

    def _reconcile_run_start_or_reject(
        self,
        *,
        run_id: str,
        command: RunInstrumentProvisionCommand,
        context: RunContext,
        config: ConfigProfileSnapshot,
        instrument_ids: tuple[str, ...],
        runtime: OwnershipRuntime,
        observed_state: tuple[InstrumentStateSnapshot, ...],
    ) -> tuple[InstrumentStateSnapshot, ...] | RunInstrumentProvisionReceipt:
        try:
            return self._reconcile_run_start(
                instrument_ids=instrument_ids,
                specs={
                    spec.id: spec for spec in config.instrument_registry.instruments
                },
                runtime=runtime,
                observed_state=observed_state,
                operation_id=command.operation_id,
            )
        except DefaultStateReconciliationRejected as error:
            # A rejection is conclusive. Earlier confirmed changes stay
            # observable, and the next owner refreshes them before acting.
            if release_instruments(runtime.instruments.values()):
                raise BackendConflict(
                    "instrument default-state reconciliation rejection "
                    "could not be released"
                ) from error
            return self._reject_run_provision(
                run_id,
                command,
                context=context,
                problems=error.problems,
            )
        except DefaultStateReconciliationUnknown as error:
            self._emergency_fault_ownership(
                runtime,
                operation_id="hardware.provision.unknown",
            )
            self._mark_run_unknown(
                run_id,
                token=command.lease_id,
                reason="run_instrument_default_reconciliation_unknown",
            )
            raise BackendConflict(
                "instrument default-state reconciliation at run start "
                "failed with unknown state"
            ) from error

    @staticmethod
    def _reconcile_run_start(
        *,
        instrument_ids: tuple[str, ...],
        specs: Mapping[str, InstrumentSpec],
        runtime: OwnershipRuntime,
        observed_state: tuple[InstrumentStateSnapshot, ...],
        operation_id: str,
    ) -> tuple[InstrumentStateSnapshot, ...]:
        observed = {
            state.instrument_id: state.model_copy(deep=True) for state in observed_state
        }
        commands: list[InstrumentStateCommand] = []
        for instrument_id in instrument_ids:
            spec = specs[instrument_id]
            if spec.run_start != "apply_default_state":
                continue
            assignments = configured_state_assignments(
                instrument_id=spec.id,
                configured_state=spec.default_state,
                instrument=runtime.instruments[instrument_id],
            )
            command = pending_configured_state_command(
                instrument_id=instrument_id,
                assignments=assignments,
                instrument=runtime.instruments[instrument_id],
                observed_state=observed[instrument_id],
                operation_id=f"{operation_id}.default_state.{instrument_id}",
            )
            if command is not None:
                commands.append(command)

        reconciled = {
            instrument_id: state.model_copy(deep=True)
            for instrument_id, state in observed.items()
        }
        for command in commands:
            instrument_id = command.instrument_id
            instrument = runtime.instruments[instrument_id]
            try:
                receipt = execute_instrument_apply(
                    instrument,
                    lower_backend_apply_request(command),
                    assignments=command.assignments,
                )
            except InstrumentCommandExecutionError as error:
                raise DefaultStateReconciliationUnknown from error
            if receipt.status == "unknown":
                raise DefaultStateReconciliationUnknown
            if receipt.status != "applied":
                raise DefaultStateReconciliationRejected(
                    problems=receipt.problems,
                )
            state = instrument.assumed_state
            assert state is not None
            reconciled[instrument_id] = state.model_copy(deep=True)
        return tuple(reconciled[instrument_id] for instrument_id in instrument_ids)

    def execute_run_hardware(
        self,
        run_id: str,
        request: RunHardwareBatchCommand,
    ) -> RunHardwareBatchReceipt:
        """Execute one idempotent ordered hardware block under the run fence."""

        payload_scope = run_payload_scope(run_id, request.batch.operation_id)
        context = self._run_context_state(run_id)
        if context is None:
            raise BackendConflict("run instruments are not provisioned")
        with context.lock:
            self._fence_run(run_id, request.lease_id)
            provision = context.provision
            if provision is None or provision.receipt.status != "ready":
                raise BackendConflict("run hardware is not ready")
            next_sequence = provision.next_batch_sequence
            if request.sequence > next_sequence:
                self._payloads.release(payload_scope)
                raise BackendConflict("run hardware batch sequence has a gap")
            if request.sequence < next_sequence - 1:
                self._payloads.release(payload_scope)
                raise BackendConflict("run hardware batch receipt has expired")
            try:
                canonical_request = self._payloads.canonicalize_hardware_command(
                    request,
                    scope=payload_scope,
                )
            except CommandPayloadError:
                self._payloads.release(payload_scope)
                raise
            if request.sequence < next_sequence:
                cached = provision.latest_batch
                assert cached is not None
                cached_request, cached_receipt = cached
                if cached_request != canonical_request:
                    self._payloads.release(payload_scope)
                    raise BackendConflict(
                        "run hardware batch sequence has different operation content"
                    )
                self._payloads.release(payload_scope)
                return cached_receipt
            runtime = context.runtime
            if runtime is None:
                raise BackendConflict("run has no owned daemon instruments")
            preflight_problems = self._preflight_hardware_batch(
                run_id,
                runtime,
                canonical_request,
            )
            if preflight_problems:
                receipt = RunHardwareBatchReceipt(
                    operation_id=canonical_request.batch.operation_id,
                    problems=preflight_problems,
                )
                provision.latest_batch = (
                    canonical_request,
                    receipt,
                )
                provision.next_batch_sequence += 1
                self._payloads.release(payload_scope)
                return receipt
            try:
                materialized_payloads = self._payloads.materialize_payload_sets(
                    (
                        action.payloads if isinstance(action, RunHardwareInvoke) else {}
                        for action in canonical_request.batch.actions
                    ),
                    scope=payload_scope,
                )
            except CommandPayloadError:
                self._payloads.release(payload_scope)
                raise
            backend_requests = tuple(
                lower_hardware_action(
                    action,
                    materialized_payloads=payloads,
                )
                for action, payloads in zip(
                    canonical_request.batch.actions,
                    materialized_payloads,
                    strict=True,
                )
            )
            values: list[RunHardwareValue] = []
            state_actions: list[RunHardwareStateActionReceipt] = []
            problems: list[Problem] = []
            completed_effect_ids: list[str] = []
            effect_receipts: list[JsonValue] = []
            costs: list[RunOperationCost] = []
            indeterminate_reason: str | None = None
            with runtime.lock:
                preparation_problems, indeterminate_reason = (
                    self._prepare_hardware_acquisitions(
                        run_id,
                        context,
                        runtime,
                        canonical_request.batch.actions,
                        backend_requests,
                        costs,
                    )
                )
                problems.extend(preparation_problems)
                if not problems:
                    (
                        action_values,
                        action_problems,
                        action_effect_ids,
                        action_receipts,
                        action_state_actions,
                        indeterminate_reason,
                    ) = self._execute_hardware_actions(
                        run_id,
                        context,
                        runtime,
                        canonical_request.batch.actions,
                        backend_requests,
                        costs,
                    )
                    values.extend(action_values)
                    problems.extend(action_problems)
                    completed_effect_ids.extend(action_effect_ids)
                    effect_receipts.extend(action_receipts)
                    state_actions.extend(action_state_actions)
            receipt = RunHardwareBatchReceipt(
                operation_id=canonical_request.batch.operation_id,
                values=tuple(values),
                state_actions=tuple(state_actions),
                problems=tuple(problems),
                indeterminate=indeterminate_reason is not None,
            )
            if receipt.problems or receipt.indeterminate or costs:
                self._record_hardware_batch_problem(
                    run_id,
                    runtime,
                    canonical_request,
                    receipt,
                    completed_effect_ids=completed_effect_ids,
                    effect_receipts=effect_receipts,
                    costs=costs,
                )
            if indeterminate_reason is not None:
                self._lose_run_runtime(
                    run_id,
                    runtime,
                    token=canonical_request.lease_id,
                    reason=indeterminate_reason,
                )
                self._payloads.release(payload_scope)
                return receipt
            provision.latest_batch = (
                canonical_request,
                receipt,
            )
            provision.next_batch_sequence += 1
            self._payloads.release(payload_scope)
            return receipt

    def _record_hardware_batch_problem(
        self,
        run_id: str,
        runtime: OwnershipRuntime,
        request: RunHardwareBatchCommand,
        receipt: RunHardwareBatchReceipt,
        *,
        completed_effect_ids: Sequence[str] = (),
        effect_receipts: Sequence[JsonValue] = (),
        costs: Sequence[RunOperationCost] = (),
    ) -> None:
        receipt_evidence: dict[str, JsonValue] = {
            "sequence": request.sequence,
            "costs": [cost.model_dump(mode="json") for cost in costs],
            "completed_effect_ids": list(completed_effect_ids),
            "effect_receipts": list(effect_receipts),
            "problem_codes": [item.code for item in receipt.problems],
            "problems": [item.model_dump(mode="json") for item in receipt.problems],
            "value_ids": [value.value_id for value in receipt.values],
        }
        try:
            self._record_run_operation_event(
                run_id,
                token=request.lease_id,
                instrument_id=None,
                operation_id=request.batch.operation_id,
                event_kind=(
                    "run_hardware_batch_unknown"
                    if receipt.indeterminate
                    else "run_hardware_batch_failed"
                    if receipt.problems
                    else "run_hardware_batch_measured"
                ),
                status="unknown"
                if receipt.indeterminate
                else "failed"
                if receipt.problems
                else "completed",
                details=receipt_evidence,
            )
        except Exception as audit_error:
            try:
                self._lose_run_runtime(
                    run_id,
                    runtime,
                    token=request.lease_id,
                    reason="run_hardware_batch_audit_unknown",
                )
            except Exception as quarantine_error:
                raise audit_error from quarantine_error
            raise

    def _preflight_hardware_batch(
        self,
        run_id: str,
        runtime: OwnershipRuntime,
        request: RunHardwareBatchCommand,
    ) -> tuple[Problem, ...]:
        """Validate the complete batch before the first hardware side effect."""

        problems: list[Problem] = []
        assumed_states: dict[
            str,
            InstrumentStateSnapshot | ProjectedInstrumentState | None,
        ] = {
            instrument_id: instrument.assumed_state
            for instrument_id, instrument in runtime.instruments.items()
        }
        for action in request.batch.actions:
            if (
                action.instrument_id not in runtime.instruments
                or action.instrument_id not in assumed_states
            ):
                problems.append(
                    hardware_problem(
                        "hardware_instrument_not_live",
                        (
                            "instrument is not live for run "
                            f"{run_id}: {action.instrument_id}"
                        ),
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                    )
                )
                continue
            if isinstance(action, RunHardwareApply):
                command = InstrumentStateCommand(
                    command_id=action.effect_id,
                    instrument_id=action.instrument_id,
                    assignments=list(action.assignments),
                )
                description = runtime.instruments[action.instrument_id].description
                baseline = assumed_states[action.instrument_id]
                action_problems = validate_state_command(
                    command=command,
                    description=description,
                    baseline=baseline,
                )
                problems.extend(
                    contextualize_problems(
                        action_problems,
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                    )
                )
                if not action_problems:
                    assumed_states[action.instrument_id] = project_instrument_state(
                        baseline
                        or ProjectedInstrumentState(
                            instrument_id=action.instrument_id,
                        ),
                        command,
                        description=description,
                    )
            elif isinstance(action, RunHardwareInvoke):
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
                action_problems = validate_invoke_command(
                    command=command,
                    description=runtime.instruments[action.instrument_id].description,
                )
                action_problems.extend(
                    payload_codec_problems(
                        command.payloads,
                        runtime.payload_catalog,
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                    )
                )
                problems.extend(
                    contextualize_problems(
                        action_problems,
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                    )
                )
                if not action_problems:
                    baseline = assumed_states[action.instrument_id]
                    if baseline is not None:
                        assumed_states[action.instrument_id] = (
                            project_instrument_invoke_state(
                                baseline,
                                command,
                                description=runtime.instruments[
                                    action.instrument_id
                                ].description,
                            )
                        )
            else:
                command = CollectCommand(
                    command_id=action.effect_id,
                    instrument_id=action.instrument_id,
                    point_index=action.point_index,
                    point_count=action.point_count,
                    requests=list(action.requests),
                )
                problems.extend(
                    contextualize_problems(
                        validate_collect_plan(
                            command=command,
                            description=runtime.instruments[
                                action.instrument_id
                            ].description,
                            baseline=assumed_states[action.instrument_id],
                        ),
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                    )
                )
        return tuple(problems)

    def _execute_hardware_actions(
        self,
        run_id: str,
        context: RunContext,
        runtime: OwnershipRuntime,
        actions: Sequence[RunHardwareAction],
        backend_requests: Sequence[
            BackendApplyRequest | BackendInvokeRequest | BackendCollectRequest
        ],
        costs: list[RunOperationCost],
    ) -> tuple[
        tuple[RunHardwareValue, ...],
        tuple[Problem, ...],
        tuple[str, ...],
        tuple[JsonValue, ...],
        tuple[RunHardwareStateActionReceipt, ...],
        str | None,
    ]:
        values: list[RunHardwareValue] = []
        completed_effect_ids: list[str] = []
        effect_receipts: list[JsonValue] = []
        state_actions: list[RunHardwareStateActionReceipt] = []
        for action, backend_request in zip(actions, backend_requests, strict=True):
            try:
                if isinstance(action, RunHardwareApply):
                    state_action = self._execute_hardware_apply(
                        run_id,
                        runtime,
                        action,
                        cast("BackendApplyRequest", backend_request),
                        costs,
                    )
                    state_actions.append(state_action)
                    evidence: JsonValue = {
                        "effect_id": state_action.operation_id,
                        "status": state_action.status,
                        "metadata": dict(state_action.metadata),
                    }
                elif isinstance(action, RunHardwareInvoke):
                    evidence = self._execute_hardware_invoke(
                        run_id,
                        runtime,
                        action,
                        cast("BackendInvokeRequest", backend_request),
                        costs,
                    )
                else:
                    collected, evidence = self._execute_hardware_collect(
                        run_id,
                        runtime,
                        action,
                        cast("BackendCollectRequest", backend_request),
                        costs,
                    )
                    values.extend(collected)
                completed_effect_ids.append(action.effect_id)
                effect_receipts.append(evidence)
            except BackendConflict as error:
                if context.runtime is not runtime:
                    raise
                return (
                    tuple(values),
                    (
                        hardware_problem(
                            "hardware_action_failed",
                            str(error),
                            run_id=run_id,
                            operation_id=action.effect_id,
                            instrument_id=action.instrument_id,
                            point_index=action.point_index,
                        ),
                    ),
                    tuple(completed_effect_ids),
                    tuple(effect_receipts),
                    tuple(state_actions),
                    None,
                )
            except HardwareActionRejected as rejection:
                return (
                    tuple(values),
                    contextualize_problems(
                        rejection.problems,
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                    ),
                    tuple(completed_effect_ids),
                    tuple(effect_receipts),
                    tuple(state_actions),
                    None,
                )
            except HardwareActionIndeterminate as indeterminate:
                return (
                    tuple(values),
                    contextualize_problems(
                        indeterminate.problems,
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                    ),
                    tuple(completed_effect_ids),
                    tuple(effect_receipts),
                    tuple(state_actions),
                    indeterminate.reason,
                )
        return (
            tuple(values),
            (),
            tuple(completed_effect_ids),
            tuple(effect_receipts),
            tuple(state_actions),
            None,
        )

    def _prepare_hardware_acquisitions(
        self,
        run_id: str,
        context: RunContext,
        runtime: OwnershipRuntime,
        actions: Sequence[RunHardwareAction],
        backend_requests: Sequence[
            BackendApplyRequest | BackendInvokeRequest | BackendCollectRequest
        ],
        costs: list[RunOperationCost],
    ) -> tuple[tuple[Problem, ...], str | None]:
        acquisition_plans: dict[str, list[BackendCollectRequest]] = {}
        preparation_actions: dict[str, RunHardwareCollect] = {}
        for action, backend_request in zip(actions, backend_requests, strict=True):
            if not isinstance(action, RunHardwareCollect):
                continue
            acquisition_plans.setdefault(action.instrument_id, []).append(
                cast("BackendCollectRequest", backend_request)
            )
            preparation_actions.setdefault(action.instrument_id, action)
        for instrument_id, acquisitions in acquisition_plans.items():
            action = preparation_actions[instrument_id]
            try:
                self._execute_hardware_acquisition_prepare(
                    run_id,
                    runtime,
                    action,
                    BackendAcquisitionPlan(acquisitions=tuple(acquisitions)),
                    costs,
                )
            except BackendConflict as error:
                if context.runtime is not runtime:
                    raise
                return (
                    (
                        hardware_problem(
                            "hardware_action_failed",
                            str(error),
                            run_id=run_id,
                            operation_id=action.effect_id,
                            instrument_id=action.instrument_id,
                            point_index=action.point_index,
                        ),
                    ),
                    None,
                )
            except HardwareActionRejected as rejection:
                return (
                    contextualize_problems(
                        rejection.problems,
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                    ),
                    None,
                )
            except HardwareActionIndeterminate as indeterminate:
                return (
                    contextualize_problems(
                        indeterminate.problems,
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                        point_index=action.point_index,
                    ),
                    indeterminate.reason,
                )
        return (), None

    def _execute_hardware_acquisition_prepare(
        self,
        run_id: str,
        runtime: OwnershipRuntime,
        action: RunHardwareCollect,
        plan: BackendAcquisitionPlan,
        costs: list[RunOperationCost],
    ) -> None:
        instrument = runtime.instruments[action.instrument_id]
        try:
            with observe_operation(
                costs, instrument, action.effect_id, "prepare"
            ) as observation:
                receipt = execute_instrument_acquisition_prepare(instrument, plan)
                observation.receipt(receipt.status, receipt.measured_cost)
        except InstrumentCommandExecutionError as error:
            raise HardwareActionIndeterminate(
                error.problems
                or (
                    hardware_problem(
                        error.reason,
                        str(error),
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                    ),
                ),
                reason=f"run_{error.reason}",
            ) from error
        if receipt.status == "unknown":
            raise HardwareActionIndeterminate(
                receipt.problems,
                reason="run_instrument_acquisition_prepare_unknown",
            )
        if receipt.status != "prepared":
            raise HardwareActionRejected(receipt.problems)

    def _execute_hardware_apply(
        self,
        run_id: str,
        runtime: OwnershipRuntime,
        action: RunHardwareApply,
        driver_request: BackendApplyRequest,
        costs: list[RunOperationCost],
    ) -> RunHardwareStateActionReceipt:
        instrument = runtime.instruments[action.instrument_id]
        current = instrument.assumed_state
        if current is None:
            raise BackendConflict("instrument state must be synchronized before apply")
        assignments = tuple(
            (assignment, physical)
            for assignment, physical in zip(
                action.assignments,
                driver_request.assignments,
                strict=True,
            )
            if not state_assignment_satisfied(current, assignment)
        )
        if not assignments:
            return RunHardwareStateActionReceipt(
                operation_id=action.effect_id,
                instrument_id=action.instrument_id,
                point_index=action.point_index,
                status="unchanged",
            )
        command = InstrumentStateCommand(
            command_id=action.effect_id,
            instrument_id=action.instrument_id,
            assignments=[assignment for assignment, _physical in assignments],
        )
        driver_request = BackendApplyRequest(
            assignments=tuple(physical for _assignment, physical in assignments)
        )
        description = instrument.description
        validation_problems = validate_state_command(
            command=command,
            description=description,
            baseline=current,
        )
        if validation_problems:
            raise HardwareActionRejected(validation_problems)
        try:
            with observe_operation(
                costs, instrument, action.effect_id, "apply"
            ) as observation:
                receipt = execute_instrument_apply(
                    instrument,
                    driver_request,
                    assignments=command.assignments,
                )
                observation.receipt(receipt.status, receipt.measured_cost)
        except InstrumentCommandExecutionError as error:
            raise HardwareActionIndeterminate(
                error.problems
                or (
                    hardware_problem(
                        error.reason,
                        str(error),
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                    ),
                ),
                reason=f"run_{error.reason}",
            ) from error
        if receipt.status == "unknown":
            raise HardwareActionIndeterminate(
                receipt.problems,
                reason="run_instrument_apply_receipt_unknown",
            )
        if receipt.status != "applied":
            raise HardwareActionRejected(receipt.problems)
        return RunHardwareStateActionReceipt(
            operation_id=action.effect_id,
            instrument_id=action.instrument_id,
            point_index=action.point_index,
            status="applied",
            assignments=tuple(
                InstrumentStateSetting(
                    target=assignment.target,
                    value=assignment.value,
                )
                for assignment, _physical in assignments
            ),
            metadata=dict(receipt.metadata),
        )

    def _execute_hardware_invoke(
        self,
        run_id: str,
        runtime: OwnershipRuntime,
        action: RunHardwareInvoke,
        backend_request: BackendInvokeRequest,
        costs: list[RunOperationCost],
    ) -> dict[str, JsonValue]:
        instrument = runtime.instruments[action.instrument_id]
        try:
            with observe_operation(
                costs, instrument, action.effect_id, "invoke"
            ) as observation:
                receipt = execute_instrument_invoke(instrument, backend_request)
                observation.receipt(receipt.status, receipt.measured_cost)
        except InstrumentCommandExecutionError as error:
            raise HardwareActionIndeterminate(
                error.problems
                or (
                    hardware_problem(
                        error.reason,
                        str(error),
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                    ),
                ),
                reason=f"run_{error.reason}",
            ) from error
        if receipt.status == "unknown":
            raise HardwareActionIndeterminate(
                receipt.problems,
                reason="run_instrument_invoke_receipt_unknown",
            )
        if receipt.status != "invoked":
            raise HardwareActionRejected(receipt.problems)
        return {
            "effect_id": action.effect_id,
            "status": receipt.status,
            "metadata": dict(receipt.metadata),
        }

    def _execute_hardware_collect(
        self,
        run_id: str,
        runtime: OwnershipRuntime,
        action: RunHardwareCollect,
        driver_request: BackendCollectRequest,
        costs: list[RunOperationCost],
    ) -> tuple[tuple[RunHardwareValue, ...], dict[str, JsonValue]]:
        command = CollectCommand(
            command_id=action.effect_id,
            instrument_id=action.instrument_id,
            point_index=action.point_index,
            point_count=action.point_count,
            requests=list(action.requests),
        )
        instrument = runtime.instruments[action.instrument_id]
        validation_problems = validate_collect_command(
            command=command,
            description=instrument.description,
        )
        if validation_problems:
            raise HardwareActionRejected(validation_problems)
        started_at = datetime.now(UTC)
        try:
            with observe_operation(
                costs, instrument, action.effect_id, "collect"
            ) as observation:
                receipt = execute_instrument_collect(
                    instrument,
                    driver_request,
                    command=command,
                )
                observation.receipt(receipt.status, receipt.measured_cost)
        except InstrumentCommandExecutionError as error:
            if error.reason == "instrument_collect_receipt_invalid":
                raise HardwareActionRejected(error.problems) from error
            raise HardwareActionIndeterminate(
                error.problems
                or (
                    hardware_problem(
                        error.reason,
                        str(error),
                        run_id=run_id,
                        operation_id=action.effect_id,
                        instrument_id=action.instrument_id,
                    ),
                ),
                reason=f"run_{error.reason}",
            ) from error
        completed_at = datetime.now(UTC)
        if receipt.status == "unknown":
            raise HardwareActionIndeterminate(
                receipt.problems,
                reason="run_instrument_collect_receipt_unknown",
            )
        if receipt.status == "not_collected":
            raise HardwareActionRejected(receipt.problems)
        assert receipt.readback is not None
        bindings = {
            binding.request_id: binding.value_ids for binding in action.bindings
        }
        requests = {request.id: request for request in action.requests}
        if set(receipt.readback.values) != set(bindings):
            raise BackendConflict(
                "instrument acquisition results do not match hardware batch bindings"
            )
        values = tuple(
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
        return values, {
            "effect_id": action.effect_id,
            "status": receipt.status,
            "metadata": dict(receipt.metadata),
            "readback_metadata": dict(receipt.readback.metadata),
        }

    def finish_run_hardware(
        self,
        run_id: str,
        command: RunHardwareFinishCommand,
    ) -> RunHardwareFinalizationReceipt:
        """Release run ownership once and return terminal readback."""

        context = self._run_context_state(run_id)
        if context is None:
            raise BackendConflict("run instruments are not provisioned")
        with context.lock:
            finalization = context.finalization
            if finalization is not None:
                if isinstance(finalization, RunFinalizing):
                    raise BackendConflict("run instrument host is finalizing")
                if finalization.command != command:
                    raise BackendConflict(
                        "run hardware was finalized with different content"
                    )
                self._payloads.release_owner("run", run_id)
                return finalization.receipt

            self._fence_run(run_id, command.lease_id)
            finalization_started = perf_counter()
            runtime = context.runtime
            if runtime is None:
                provision = context.provision
                if (
                    provision is None
                    or provision.receipt.status != "ready"
                    or provision.receipt.instrument_ids
                ):
                    raise BackendConflict("run has no owned daemon instruments")
                receipt = RunHardwareFinalizationReceipt(
                    operation_id=command.operation_id,
                )
            else:
                context.finalization = RunFinalizing()
                provision = context.provision
                if provision is None or provision.receipt.status != "ready":
                    raise BackendConflict("run instruments are not provisioned")
                final_state, actions, problems = self._finalize_run_instruments(
                    run_id,
                    token=command.lease_id,
                    runtime=runtime,
                    baseline_state=provision.receipt.baseline_state,
                    failed=command.failed,
                    operation_id=command.operation_id,
                )
                receipt = RunHardwareFinalizationReceipt(
                    operation_id=command.operation_id,
                    actions=tuple(actions),
                    final_state=tuple(final_state),
                    problems=tuple(problems),
                )

            context.provision = None
            context.runtime = None
            context.finalization = RunFinalized(
                command=command,
                receipt=receipt,
            )
            self._payloads.release_owner("run", run_id)
            self._record_run_operation_event(
                run_id,
                token=command.lease_id,
                instrument_id=None,
                operation_id=command.operation_id,
                event_kind="run_hardware_finalization_measured",
                status="completed",
                details={
                    "finalization_cost": RunFinalizationCost(
                        operation_id=command.operation_id,
                        seconds=perf_counter() - finalization_started,
                    ).model_dump(mode="json")
                },
            )
            return receipt

    def _finalize_run_instruments(
        self,
        run_id: str,
        *,
        token: str,
        runtime: OwnershipRuntime,
        baseline_state: Sequence[InstrumentStateSnapshot],
        failed: bool,
        operation_id: str,
    ) -> tuple[
        list[InstrumentStateSnapshot],
        list[RunHardwareFinalizationActionReceipt],
        list[Problem],
    ]:
        problems: list[Problem] = []
        actions: list[RunHardwareFinalizationActionReceipt] = []
        required_safe_instruments: set[str] = set()
        with runtime.lock:
            if not failed:
                restore_problems, restore_actions = (
                    self._restore_baseline_or_quarantine(
                        run_id,
                        token=token,
                        runtime=runtime,
                        baseline_state=baseline_state,
                        operation_id=operation_id,
                    )
                )
                actions.extend(restore_actions)
                if restore_problems:
                    problems.extend(restore_problems)
                    failed = True
            if not failed:
                safe_problems = self._apply_safe_states_or_quarantine(
                    run_id,
                    token=token,
                    runtime=runtime,
                    operation_id=operation_id,
                    selected=lambda spec: spec.success_action == "apply_safe_state",
                    actions=actions,
                    required_safe_instruments=required_safe_instruments,
                )
                if safe_problems:
                    problems.extend(safe_problems)
                    failed = True
            if failed:
                for instrument_id in reversed(tuple(runtime.instruments)):
                    try:
                        runtime.instruments[instrument_id].abort()
                    except Exception as error:
                        try:
                            self._record_cleanup_failure(
                                run_id,
                                token=token,
                                instrument_id=instrument_id,
                                operation="abort",
                                operation_id=f"{operation_id}.abort.{instrument_id}",
                                error=error,
                            )
                        finally:
                            self._lose_run_runtime(
                                run_id,
                                runtime,
                                token=token,
                                reason="run_instrument_abort_unknown",
                                abort=False,
                            )
                        raise BackendConflict(
                            "instrument abort failed with unknown state"
                        ) from error
                    actions.append(
                        RunHardwareFinalizationActionReceipt(
                            operation_id=f"{operation_id}.abort.{instrument_id}",
                            instrument_id=instrument_id,
                            kind="abort",
                            status="completed",
                        )
                    )
                problems.extend(
                    self._apply_safe_states_or_quarantine(
                        run_id,
                        token=token,
                        runtime=runtime,
                        operation_id=operation_id,
                        selected=(
                            lambda spec: spec.failure_action == "abort_then_safe_state"
                        ),
                        actions=actions,
                        required_safe_instruments=required_safe_instruments,
                    )
                )
            final_state: list[InstrumentStateSnapshot] = []
            faulted: set[str] = set()
            for instrument_id, instrument in runtime.instruments.items():
                try:
                    final_state.append(observe_instrument(instrument))
                except BackendConflict as error:
                    if instrument_id in required_safe_instruments:
                        self._lose_run_runtime(
                            run_id,
                            runtime,
                            token=token,
                            reason="run_instrument_required_safe_state_unconfirmed",
                            abort=False,
                        )
                        raise BackendConflict(
                            "required instrument safe state could not be confirmed"
                        ) from error
                    faulted.add(instrument_id)
                    with suppress(Exception):
                        instrument.fault()
                    problems.append(
                        hardware_problem(
                            "instrument_terminal_read_failed",
                            str(error),
                            run_id=run_id,
                            operation_id=operation_id,
                            instrument_id=instrument_id,
                        )
                    )
            if release_instruments(
                instrument
                for instrument_id, instrument in runtime.instruments.items()
                if instrument_id not in faulted
            ):
                self._lose_run_runtime(
                    run_id,
                    runtime,
                    token=token,
                    reason="run_instrument_release_failed",
                )
                raise BackendConflict("instrument ownership release failed")
        self._pop_run_runtime(run_id, expected=runtime)
        return final_state, actions, problems

    def _restore_baseline_or_quarantine(
        self,
        run_id: str,
        *,
        token: str,
        runtime: OwnershipRuntime,
        baseline_state: Sequence[InstrumentStateSnapshot],
        operation_id: str,
    ) -> tuple[list[Problem], list[RunHardwareFinalizationActionReceipt]]:
        actions: list[RunHardwareFinalizationActionReceipt] = []
        baseline_by_instrument = {
            state.instrument_id: state for state in baseline_state
        }
        for instrument_id, instrument in runtime.instruments.items():
            spec = runtime.specs[instrument_id]
            if spec.success_action != "restore_baseline":
                continue
            restore_operation_id = f"{operation_id}.restore_baseline.{instrument_id}"
            try:
                observed_state = observe_instrument(instrument)
                assignments = restorable_state_assignments(
                    instrument_id=instrument_id,
                    baseline_state=baseline_by_instrument[instrument_id],
                    instrument=instrument,
                )
                command = pending_configured_state_command(
                    instrument_id=instrument_id,
                    assignments=assignments,
                    instrument=instrument,
                    observed_state=observed_state,
                    operation_id=restore_operation_id,
                )
                if command is None:
                    instrument.adopt_state(observed_state)
                    actions.append(
                        RunHardwareFinalizationActionReceipt(
                            operation_id=restore_operation_id,
                            instrument_id=instrument_id,
                            kind="restore_baseline",
                            status="unchanged",
                        )
                    )
                    continue
                receipt = execute_instrument_apply(
                    instrument,
                    lower_backend_apply_request(command),
                    assignments=command.assignments,
                )
            except DefaultStateReconciliationRejected as rejection:
                contextualized = tuple(
                    contextualize_problems(
                        rejection.problems,
                        run_id=run_id,
                        operation_id=restore_operation_id,
                        instrument_id=instrument_id,
                    )
                )
                actions.append(
                    RunHardwareFinalizationActionReceipt(
                        operation_id=restore_operation_id,
                        instrument_id=instrument_id,
                        kind="restore_baseline",
                        status="rejected",
                        problems=contextualized,
                    )
                )
                return list(contextualized), actions
            except (BackendConflict, InstrumentCommandExecutionError) as error:
                self._lose_run_runtime(
                    run_id,
                    runtime,
                    token=token,
                    reason="run_instrument_baseline_restore_unknown",
                )
                raise BackendConflict(
                    "instrument baseline restore failed with unknown state"
                ) from error
            if receipt.status == "unknown":
                self._lose_run_runtime(
                    run_id,
                    runtime,
                    token=token,
                    reason="run_instrument_baseline_restore_unknown",
                )
                raise BackendConflict(
                    "instrument baseline restore failed with unknown state"
                )
            if receipt.status != "applied":
                contextualized = tuple(
                    contextualize_problems(
                        receipt.problems,
                        run_id=run_id,
                        operation_id=restore_operation_id,
                        instrument_id=instrument_id,
                    )
                )
                actions.append(
                    RunHardwareFinalizationActionReceipt(
                        operation_id=restore_operation_id,
                        instrument_id=instrument_id,
                        kind="restore_baseline",
                        status="rejected",
                        metadata=receipt.metadata,
                        problems=contextualized,
                    )
                )
                return list(contextualized), actions
            actions.append(
                RunHardwareFinalizationActionReceipt(
                    operation_id=restore_operation_id,
                    instrument_id=instrument_id,
                    kind="restore_baseline",
                    status="completed",
                    metadata=receipt.metadata,
                )
            )
        return [], actions

    def _apply_safe_states_or_quarantine(
        self,
        run_id: str,
        *,
        token: str,
        runtime: OwnershipRuntime,
        operation_id: str,
        selected: Callable[[InstrumentSpec], bool],
        actions: list[RunHardwareFinalizationActionReceipt],
        required_safe_instruments: set[str],
    ) -> list[Problem]:
        problems: list[Problem] = []
        for instrument_id, instrument in runtime.instruments.items():
            spec = runtime.specs[instrument_id]
            if not selected(spec):
                continue
            if spec.safe_state_requirement == "required":
                required_safe_instruments.add(instrument_id)
            rejected = False
            for index, operation in enumerate(spec.safe_operations):
                safe_operation_id = (
                    f"{operation_id}.safe_operation.{instrument_id}.{index}"
                )
                command = self._configured_safe_operation_command(
                    instrument_id,
                    operation,
                    operation_id=safe_operation_id,
                )
                try:
                    receipt = execute_instrument_invoke(
                        instrument,
                        lower_backend_invoke_request(
                            command,
                            materialized_payloads={},
                        ),
                    )
                except InstrumentCommandExecutionError as error:
                    self._lose_run_runtime(
                        run_id,
                        runtime,
                        token=token,
                        reason="run_instrument_safe_operation_unknown",
                        abort=False,
                    )
                    raise BackendConflict(
                        "instrument safe operation failed with unknown state"
                    ) from error
                if receipt.status == "unknown":
                    self._lose_run_runtime(
                        run_id,
                        runtime,
                        token=token,
                        reason="run_instrument_safe_operation_unknown",
                        abort=False,
                    )
                    raise BackendConflict(
                        "instrument safe operation failed with unknown state"
                    )
                if receipt.status != "invoked":
                    contextualized = tuple(
                        contextualize_problems(
                            receipt.problems,
                            run_id=run_id,
                            operation_id=safe_operation_id,
                            instrument_id=instrument_id,
                        )
                    )
                    actions.append(
                        RunHardwareFinalizationActionReceipt(
                            operation_id=safe_operation_id,
                            instrument_id=instrument_id,
                            kind="safe_operation",
                            status="rejected",
                            metadata=receipt.metadata,
                            problems=contextualized,
                        )
                    )
                    if spec.safe_state_requirement == "required":
                        self._lose_run_runtime(
                            run_id,
                            runtime,
                            token=token,
                            reason="run_instrument_required_safe_state_rejected",
                        )
                        raise BackendConflict(
                            "required instrument safe operation was rejected"
                        )
                    problems.extend(contextualized)
                    rejected = True
                    break
                actions.append(
                    RunHardwareFinalizationActionReceipt(
                        operation_id=safe_operation_id,
                        instrument_id=instrument_id,
                        kind="safe_operation",
                        status="completed",
                        metadata=receipt.metadata,
                    )
                )
            if rejected or not spec.safe_state:
                continue
            safe_state_operation_id = f"{operation_id}.safe_state.{instrument_id}"
            try:
                observed_state = observe_instrument(instrument)
                assignments = configured_state_assignments(
                    instrument_id=spec.id,
                    configured_state=spec.safe_state,
                    instrument=instrument,
                )
                command = pending_configured_state_command(
                    instrument_id=instrument_id,
                    assignments=assignments,
                    instrument=instrument,
                    observed_state=observed_state,
                    operation_id=safe_state_operation_id,
                )
                if command is None:
                    instrument.adopt_state(observed_state)
                    actions.append(
                        RunHardwareFinalizationActionReceipt(
                            operation_id=safe_state_operation_id,
                            instrument_id=instrument_id,
                            kind="safe_state",
                            status="unchanged",
                        )
                    )
                    continue
                receipt = execute_instrument_apply(
                    instrument,
                    lower_backend_apply_request(command),
                    assignments=command.assignments,
                )
            except (
                BackendConflict,
                InstrumentCommandExecutionError,
            ) as error:
                self._lose_run_runtime(
                    run_id,
                    runtime,
                    token=token,
                    reason="run_instrument_safe_state_unknown",
                    abort=False,
                )
                raise BackendConflict(
                    "instrument safe-state recovery failed with unknown state"
                ) from error
            if receipt.status == "unknown":
                self._lose_run_runtime(
                    run_id,
                    runtime,
                    token=token,
                    reason="run_instrument_safe_state_unknown",
                    abort=False,
                )
                raise BackendConflict(
                    "instrument safe-state recovery failed with unknown state"
                )
            if receipt.status != "applied":
                contextualized = tuple(
                    contextualize_problems(
                        receipt.problems,
                        run_id=run_id,
                        operation_id=safe_state_operation_id,
                        instrument_id=instrument_id,
                    )
                )
                actions.append(
                    RunHardwareFinalizationActionReceipt(
                        operation_id=safe_state_operation_id,
                        instrument_id=instrument_id,
                        kind="safe_state",
                        status="rejected",
                        metadata=receipt.metadata,
                        problems=contextualized,
                    )
                )
                if spec.safe_state_requirement == "required":
                    self._lose_run_runtime(
                        run_id,
                        runtime,
                        token=token,
                        reason="run_instrument_required_safe_state_rejected",
                    )
                    raise BackendConflict("required instrument safe state was rejected")
                problems.extend(contextualized)
                continue
            actions.append(
                RunHardwareFinalizationActionReceipt(
                    operation_id=safe_state_operation_id,
                    instrument_id=instrument_id,
                    kind="safe_state",
                    status="completed",
                    metadata=receipt.metadata,
                )
            )
        return problems

    @staticmethod
    def _configured_safe_operation_command(
        instrument_id: str,
        operation: InstrumentSafeOperation,
        *,
        operation_id: str,
    ) -> InvokeCommand:
        return InvokeCommand(
            command_id=operation_id,
            instrument_id=instrument_id,
            resource_id=instrument_id,
            interface_id=operation.interface_id,
            component_path=list(operation.component_path),
            operation_id=operation.operation_id,
            arguments=[
                InstrumentOperationArgument(id=argument.id, value=argument.value)
                for argument in operation.arguments
            ],
        )

    def _emergency_fault_ownership(
        self,
        runtime: OwnershipRuntime,
        *,
        operation_id: str,
    ) -> bool:
        """Attempt daemon-owned failure actions before discarding a lost owner."""

        failed = abort_instruments(runtime.instruments.values())
        for instrument_id, instrument in runtime.instruments.items():
            spec = runtime.specs[instrument_id]
            if spec.failure_action != "abort_then_safe_state":
                continue
            rejected = False
            for index, operation in enumerate(spec.safe_operations):
                command = self._configured_safe_operation_command(
                    instrument_id,
                    operation,
                    operation_id=(
                        f"{operation_id}.safe_operation.{instrument_id}.{index}"
                    ),
                )
                try:
                    receipt = execute_instrument_invoke(
                        instrument,
                        lower_backend_invoke_request(
                            command,
                            materialized_payloads={},
                        ),
                    )
                except Exception:
                    failed = True
                    rejected = True
                    break
                if receipt.status != "invoked":
                    failed = True
                    rejected = True
                    break
            if rejected or not spec.safe_state:
                continue
            try:
                observed_state = observe_instrument(instrument)
                assignments = configured_state_assignments(
                    instrument_id=instrument_id,
                    configured_state=spec.safe_state,
                    instrument=instrument,
                )
                command = pending_configured_state_command(
                    instrument_id=instrument_id,
                    assignments=assignments,
                    instrument=instrument,
                    observed_state=observed_state,
                    operation_id=f"{operation_id}.safe_state.{instrument_id}",
                )
                if command is not None:
                    receipt = execute_instrument_apply(
                        instrument,
                        lower_backend_apply_request(command),
                        assignments=command.assignments,
                    )
                    failed = failed or receipt.status != "applied"
            except Exception:
                failed = True
        return fault_ownership(runtime, abort=False) or failed

    def _create_run_context(self, run_id: str) -> RunContext:
        with self._run_lock:
            return self._run_contexts.setdefault(run_id, RunContext())

    def _store_run_provision(
        self,
        run_id: str,
        context: RunContext,
        provision: RunProvision,
        *,
        runtime: OwnershipRuntime | None = None,
    ) -> None:
        with self._lifecycle_lock:
            if self._stopping:
                if runtime is not None:
                    self._emergency_fault_ownership(
                        runtime,
                        operation_id="hardware.daemon_shutdown",
                    )
                self._mark_run_unknown(
                    run_id,
                    token=provision.command.lease_id,
                    reason="daemon_shutting_down",
                )
                raise BackendConflict("instrument service is shutting down")
            with self._run_lock:
                if self._run_contexts.get(run_id) is not context:
                    if runtime is not None:
                        self._emergency_fault_ownership(
                            runtime,
                            operation_id="hardware.provision.owner_loss",
                        )
                    raise BackendConflict("run instrument context is no longer active")
                context.provision = provision
                context.runtime = runtime

    def _pop_run_runtime(
        self,
        run_id: str,
        *,
        expected: OwnershipRuntime | None = None,
    ) -> OwnershipRuntime | None:
        with self._run_lock:
            context = self._run_contexts.get(run_id)
            if context is None:
                return None
            runtime = context.runtime
            if expected is not None and runtime is not expected:
                return None
            context.runtime = None
            return runtime

    def open_session(
        self,
        command: InstrumentSessionOpenCommand,
    ) -> InstrumentSessionOpenReceipt:
        with self._open_lock:
            self._require_running()
            return self._open_session(command)

    def _open_session(
        self,
        command: InstrumentSessionOpenCommand,
    ) -> InstrumentSessionOpenReceipt:
        # Recover before reading active config so retries retain the first resolution.
        try:
            existing = self._control.get_instrument_session_by_open_operation_id(
                command.operation_id
            )
        except ControlPlaneNotFound:
            pass
        else:
            return self._replay_session_open(command, existing)

        active = self._config.get_active_config()
        configured = {
            spec.id: spec for spec in active.config.instrument_registry.instruments
        }
        temporary = {binding.id: binding for binding in command.temporary_bindings}
        collisions = tuple(
            instrument_id for instrument_id in temporary if instrument_id in configured
        )
        if collisions:
            raise BackendConflict(
                "temporary instrument ids already exist in the active config: "
                + ", ".join(collisions)
            )
        missing = tuple(
            instrument_id
            for instrument_id in command.instrument_ids
            if instrument_id not in configured and instrument_id not in temporary
        )
        if missing:
            raise BackendNotFound(f"instrument was not found: {', '.join(missing)}")
        endpoint = self._endpoint
        if endpoint is None:
            raise BackendConflict("project does not configure an instrument backend")
        configured_ids = tuple(
            instrument_id
            for instrument_id in command.instrument_ids
            if instrument_id in configured
        )
        descriptions: dict[str, InstrumentDescription] = {}
        if configured_ids:
            catalog = self.resolve_instrument_contracts(active.config)
            descriptions.update(
                self._selected_descriptions(
                    catalog,
                    config=active.config,
                    instrument_ids=configured_ids,
                )
            )
        if temporary:
            for binding in temporary.values():
                self._require_supported_binding(endpoint, binding)
            descriptions.update(
                self._describe_temporary_bindings(
                    endpoint,
                    bindings=tuple(temporary.values()),
                )
            )
        selected_bindings = {
            binding.id: binding for binding in instrument_bindings(active.config)
        }
        selected_bindings.update(temporary)
        selected_specs = dict(configured)
        selected_specs.update(
            {
                binding.id: self._temporary_instrument_spec(
                    binding,
                    configured=tuple(configured.values()),
                )
                for binding in temporary.values()
            }
        )
        try:
            session = self._control.open_instrument_session(
                operation_id=command.operation_id,
                actor=command.actor,
                config_entry_id=active.entry.id,
                config_content_hash=active.entry.content_hash,
                instrument_ids=command.instrument_ids,
                exclusivity_keys=tuple(
                    selected_specs[instrument_id].exclusivity_key
                    for instrument_id in command.instrument_ids
                ),
                expected_config_generation=active.activation.generation,
                ttl=self._session_lease_ttl,
            )
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error

        try:
            runtime, _ = self._open_ownership(
                endpoint=endpoint,
                bindings=selected_bindings,
                specs=selected_specs,
                owner=InstrumentOwnerKey(
                    kind="instrument_session",
                    owner_id=session.session_id,
                ),
                instrument_ids=command.instrument_ids,
                expected=descriptions,
                payload_catalog=endpoint.payload_catalog,
            )
        except InstrumentBackendRejected as error:
            self._abort_open_session(session)
            raise BackendConflict(str(error)) from error
        except InstrumentBackendUnavailable as error:
            self._abort_open_session(session)
            raise BackendConflict(
                "instrument connection could not be established"
            ) from error
        except BackendConflict as error:
            self._abort_open_session(session)
            raise BackendConflict("instrument state could not be observed") from error
        try:
            # The client cannot heartbeat until open returns, so a successful
            # acquisition starts with a fresh renewal window.
            session = self._renew_open_session(session)
            with self._lifecycle_lock:
                if self._stopping:
                    raise BackendConflict("instrument service is shutting down")
                with self._sessions_lock:
                    self._sessions[session.session_id] = SessionContext(runtime)
        except BackendConflict:
            fault_ownership(runtime, abort=False)
            self._abort_open_session(session)
            raise
        return self._wire_session(session, runtime)

    def _replay_session_open(
        self,
        command: InstrumentSessionOpenCommand,
        existing: InstrumentSession,
    ) -> InstrumentSessionOpenReceipt:
        try:
            session = self._control.open_instrument_session(
                operation_id=command.operation_id,
                actor=command.actor,
                config_entry_id=existing.config_entry_id,
                config_content_hash=existing.config_content_hash,
                instrument_ids=command.instrument_ids,
                exclusivity_keys=existing.exclusivity_keys,
                expected_config_generation=None,
                ttl=self._session_lease_ttl,
            )
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error
        runtime = self._live_runtime(session.session_id)
        pinned = self._pinned_session_config(session)
        configured_ids = {spec.id for spec in pinned.instrument_registry.instruments}
        runtime_temporary_ids = set(runtime.bindings) - configured_ids
        requested_temporary_ids = {binding.id for binding in command.temporary_bindings}
        if runtime_temporary_ids != requested_temporary_ids or any(
            runtime.bindings.get(binding.id) != binding
            for binding in command.temporary_bindings
        ):
            raise BackendConflict(
                "instrument session open operation has different temporary bindings"
            )
        return self._wire_session(session, runtime)

    def _renew_open_session(self, session: InstrumentSession) -> InstrumentSession:
        try:
            return self._control.renew_instrument_session(
                session.session_id,
                ttl=self._session_lease_ttl,
            )
        except ControlPlaneNotFound as error:
            raise BackendConflict(str(error)) from error
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error

    def renew_session(self, session_id: str) -> InstrumentSessionLeaseReceipt:
        """Renew direct ownership without touching instrument state."""

        self._require_running()
        try:
            context = self._live_session_context(session_id)
        except BackendConflict:
            try:
                self._control.validate_instrument_session(session_id)
            except ControlPlaneNotFound as error:
                raise BackendNotFound(str(error)) from error
            except ControlPlaneConflict as error:
                raise BackendConflict(str(error)) from error
            raise
        with context.lease_lock:
            try:
                session = self._control.renew_instrument_session(
                    session_id,
                    ttl=self._session_lease_ttl,
                )
            except ControlPlaneNotFound as error:
                raise BackendNotFound(str(error)) from error
            except ControlPlaneConflict as error:
                raise BackendConflict(str(error)) from error
        return self._wire_session_lease(session)

    def _abort_open_session(self, session: InstrumentSession) -> None:
        try:
            self._control.close_instrument_session(
                session.session_id,
                status="aborted",
            )
        except Exception as error:
            self._mark_unknown(
                session,
                reason="instrument_session_open_cleanup_failed",
            )
            raise BackendConflict(
                "instrument session acquisition could not be released"
            ) from error

    def read_state(
        self,
        session_id: str,
        instrument_id: str,
    ) -> InstrumentStateSnapshot:
        runtime = self._live_runtime(session_id)
        with runtime.lock:
            session, _runtime, instrument = self._session_instrument(
                session_id,
                instrument_id,
            )
            return self._synchronize_session_instrument(
                session,
                runtime,
                instrument,
            )

    def read_state_members(
        self,
        session_id: str,
        instrument_id: str,
        command: InstrumentStateReadCommand,
    ) -> InstrumentStateReadback:
        runtime = self._live_runtime(session_id)
        with runtime.lock:
            _session, _runtime, instrument = self._session_instrument(
                session_id,
                instrument_id,
            )
            return observe_members(
                instrument,
                tuple(state_member_ref(target) for target in command.targets),
            )

    def observed_state_members(
        self,
        session_id: str,
        instrument_id: str,
        command: InstrumentStateReadCommand,
    ) -> InstrumentStateCacheReadback:
        runtime = self._live_runtime(session_id)
        with runtime.lock:
            _session, _runtime, instrument = self._session_instrument(
                session_id,
                instrument_id,
            )
            return observed_members(
                instrument,
                tuple(state_member_ref(target) for target in command.targets),
            )

    def _synchronize_session_instrument(
        self,
        session: InstrumentSession,
        runtime: OwnershipRuntime,
        instrument: OwnedInstrument,
    ) -> InstrumentStateSnapshot:
        try:
            state = observe_instrument(instrument)
        except BackendConflict:
            self._end_failed_observation(session, runtime)
            raise
        instrument.adopt_state(state)
        return state

    def apply_state(
        self,
        session_id: str,
        instrument_id: str,
        command: InstrumentStateCommand,
    ) -> ApplyReceipt:
        command = command.model_copy(deep=True)
        if command.instrument_id != instrument_id:
            raise BackendConflict("instrument apply command does not match its route")
        runtime = self._live_runtime(session_id)
        with runtime.lock:
            session, _runtime, instrument = self._session_instrument(
                session_id,
                instrument_id,
            )
            command_id = command.command_id
            ledger = runtime.ledgers[instrument_id]
            replay = ledger.replay(command_id)
            if replay is not None:
                if not isinstance(replay, ApplyReplay):
                    raise BackendConflict(
                        "interactive command id was already used for another "
                        "command kind"
                    )
                if replay.command != command:
                    raise BackendConflict(
                        "interactive command id has different apply content"
                    )
                return replay.receipt

            # Replay precedes refresh so retries cannot be revalidated against
            # later front-panel state.
            state = self._synchronize_session_instrument(
                session,
                runtime,
                instrument,
            )
            validation_problems = validate_state_command(
                command=command,
                description=instrument.description,
                baseline=state,
            )
            if validation_problems:
                receipt = ApplyReceipt(
                    status="not_applied",
                    problems=tuple(validation_problems),
                )
                ledger.remember(
                    command_id,
                    ApplyReplay(
                        command=command,
                        receipt=receipt,
                    ),
                )
                return receipt

            return self._execute_interactive_apply(
                runtime,
                instrument,
                command=command,
                on_started=lambda: self._record_operation_started(
                    session,
                    instrument_id=instrument_id,
                    operation_id=command_id,
                    kind="apply",
                ),
                on_finished=lambda status: self._record_operation_finished(
                    session,
                    instrument_id=instrument_id,
                    operation_id=command_id,
                    kind="apply",
                    status=status,
                ),
                on_unknown=lambda reason: self._lose_runtime(
                    session,
                    runtime,
                    reason=reason,
                ),
            )

    def apply_configured_defaults(
        self,
        session_id: str,
        instrument_id: str,
        command: InstrumentConfiguredDefaultsApplyCommand,
    ) -> InstrumentConfiguredDefaultsApplyReceipt:
        """Apply sparse defaults from the config entry pinned by this session."""

        runtime = self._live_runtime(session_id)
        with runtime.lock:
            session, _runtime, instrument = self._session_instrument(
                session_id,
                instrument_id,
            )
            ledger = runtime.ledgers[instrument_id]
            replay = ledger.replay(command.operation_id)
            if replay is not None:
                if not isinstance(replay, ConfiguredDefaultsReplay):
                    raise BackendConflict(
                        "interactive command id was already used for another "
                        "command kind"
                    )
                if replay.command != command:
                    raise BackendConflict(
                        "interactive command id has different configured-default "
                        "content"
                    )
                return replay.receipt

            spec = runtime.specs[instrument_id]
            if not spec.default_state:
                return self._reject_configured_defaults(
                    session=session,
                    runtime=runtime,
                    ledger=ledger,
                    command=command,
                    instrument_id=instrument_id,
                    problems=(
                        provision_problem(
                            "instrument_configured_defaults_missing",
                            "instrument has no configured default state",
                            operation_id=command.operation_id,
                            instrument_id=instrument_id,
                        ),
                    ),
                )
            try:
                assignments = configured_state_assignments(
                    instrument_id=spec.id,
                    configured_state=spec.default_state,
                    instrument=instrument,
                )
            except DefaultStateReconciliationRejected as error:
                return self._reject_configured_defaults(
                    session=session,
                    runtime=runtime,
                    ledger=ledger,
                    command=command,
                    instrument_id=instrument_id,
                    problems=error.problems,
                )

            try:
                observed = observe_instrument(instrument)
            except BackendConflict:
                self._end_failed_observation(session, runtime)
                raise
            instrument.adopt_state(observed)
            try:
                state_command = pending_configured_state_command(
                    instrument_id=instrument_id,
                    assignments=assignments,
                    instrument=instrument,
                    observed_state=observed,
                    operation_id=command.operation_id,
                )
            except DefaultStateReconciliationRejected as error:
                return self._reject_configured_defaults(
                    session=session,
                    runtime=runtime,
                    ledger=ledger,
                    command=command,
                    instrument_id=instrument_id,
                    problems=error.problems,
                )

            self._record_operation_started(
                session,
                instrument_id=instrument_id,
                operation_id=command.operation_id,
                kind="apply",
            )
            if state_command is None:
                return self._finish_configured_defaults(
                    session=session,
                    runtime=runtime,
                    ledger=ledger,
                    command=command,
                    receipt=InstrumentConfiguredDefaultsApplyReceipt(
                        session_id=session_id,
                        operation_id=command.operation_id,
                        instrument_id=instrument_id,
                        config_entry_id=session.config_entry_id,
                        status="unchanged",
                        state=observed,
                    ),
                )

            try:
                driver_receipt = execute_instrument_apply(
                    instrument,
                    lower_backend_apply_request(state_command),
                    assignments=state_command.assignments,
                )
            except InstrumentCommandExecutionError as error:
                reason = (
                    "instrument_configured_defaults_unknown"
                    if error.reason == "instrument_apply_unknown"
                    else "instrument_configured_defaults_state_unknown"
                )
                self._lose_runtime(
                    session,
                    runtime,
                    reason=reason,
                )
                message = (
                    "configured-default apply failed with unknown state"
                    if error.reason == "instrument_apply_unknown"
                    else str(error).replace(
                        "instrument apply",
                        "configured-default apply",
                    )
                )
                raise BackendConflict(message) from error
            if driver_receipt.status == "unknown":
                self._lose_runtime(
                    session,
                    runtime,
                    reason="instrument_configured_defaults_receipt_unknown",
                )
                raise BackendConflict(
                    "configured-default apply failed with unknown state"
                )
            if driver_receipt.status == "not_applied":
                return self._finish_configured_defaults(
                    session=session,
                    runtime=runtime,
                    ledger=ledger,
                    command=command,
                    receipt=InstrumentConfiguredDefaultsApplyReceipt(
                        session_id=session_id,
                        operation_id=command.operation_id,
                        instrument_id=instrument_id,
                        config_entry_id=session.config_entry_id,
                        status="rejected",
                        problems=driver_receipt.problems,
                    ),
                )
            state = instrument.assumed_state
            assert state is not None
            return self._finish_configured_defaults(
                session=session,
                runtime=runtime,
                ledger=ledger,
                command=command,
                receipt=InstrumentConfiguredDefaultsApplyReceipt(
                    session_id=session_id,
                    operation_id=command.operation_id,
                    instrument_id=instrument_id,
                    config_entry_id=session.config_entry_id,
                    status="applied",
                    state=state,
                ),
            )

    def _reject_configured_defaults(
        self,
        *,
        session: InstrumentSession,
        runtime: OwnershipRuntime,
        ledger: InstrumentOperationLedger,
        command: InstrumentConfiguredDefaultsApplyCommand,
        instrument_id: str,
        problems: tuple[Problem, ...],
    ) -> InstrumentConfiguredDefaultsApplyReceipt:
        self._record_operation_started(
            session,
            instrument_id=instrument_id,
            operation_id=command.operation_id,
            kind="apply",
        )
        return self._finish_configured_defaults(
            session=session,
            runtime=runtime,
            ledger=ledger,
            command=command,
            receipt=InstrumentConfiguredDefaultsApplyReceipt(
                session_id=session.session_id,
                operation_id=command.operation_id,
                instrument_id=instrument_id,
                config_entry_id=session.config_entry_id,
                status="rejected",
                problems=problems,
            ),
        )

    def _finish_configured_defaults(
        self,
        *,
        session: InstrumentSession,
        runtime: OwnershipRuntime,
        ledger: InstrumentOperationLedger,
        command: InstrumentConfiguredDefaultsApplyCommand,
        receipt: InstrumentConfiguredDefaultsApplyReceipt,
    ) -> InstrumentConfiguredDefaultsApplyReceipt:
        ledger.remember(
            command.operation_id,
            ConfiguredDefaultsReplay(
                command=command,
                receipt=receipt,
            ),
        )
        try:
            self._record_operation_finished(
                session,
                instrument_id=receipt.instrument_id,
                operation_id=command.operation_id,
                kind="apply",
                status=receipt.status,
            )
        except Exception as error:
            self._lose_runtime(
                session,
                runtime,
                reason="instrument_configured_defaults_audit_unknown",
            )
            raise BackendConflict(
                "configured-default apply completed but audit recording failed"
            ) from error
        return receipt

    def invoke(
        self,
        session_id: str,
        instrument_id: str,
        command: InvokeCommand,
    ) -> InvokeReceipt:
        payload_scope = session_payload_scope(session_id, command.command_id)
        try:
            if command.instrument_id != instrument_id:
                raise BackendConflict(
                    "instrument invoke command does not match its route"
                )
            runtime = self._live_runtime(session_id)
            with runtime.lock:
                session, _runtime, instrument = self._session_instrument(
                    session_id,
                    instrument_id,
                )
                command_id = command.command_id
                return self._invoke_live(
                    runtime,
                    instrument,
                    command=command,
                    payload_scope=payload_scope,
                    conflict_scope="interactive",
                    on_started=lambda: self._record_operation_started(
                        session,
                        instrument_id=instrument_id,
                        operation_id=command_id,
                        kind="invoke",
                    ),
                    on_finished=lambda status: self._record_operation_finished(
                        session,
                        instrument_id=instrument_id,
                        operation_id=command_id,
                        kind="invoke",
                        status=status,
                    ),
                    on_unknown=lambda reason: self._lose_runtime(
                        session,
                        runtime,
                        reason=reason,
                    ),
                )
        finally:
            self._payloads.release(payload_scope)

    def collect(
        self,
        session_id: str,
        instrument_id: str,
        intent: InteractiveCollectIntent,
    ) -> CollectReceipt:
        intent = intent.model_copy(deep=True)
        if intent.instrument_id != instrument_id:
            raise BackendConflict("interactive collect intent does not match its route")
        runtime = self._live_runtime(session_id)
        with runtime.lock:
            session, _runtime, instrument = self._session_instrument(
                session_id,
                instrument_id,
            )
            ledger = runtime.ledgers[instrument_id]
            replay = ledger.replay(intent.command_id)
            if replay is not None:
                if not isinstance(
                    replay,
                    (
                        CollectReceiptReplay,
                        CollectRejectionReplay,
                        CollectFailureReplay,
                    ),
                ):
                    raise BackendConflict(
                        "interactive command id was already used for another "
                        "command kind"
                    )
                if replay.intent != intent:
                    raise BackendConflict(
                        "interactive command id has different collect content"
                    )
                if isinstance(replay, CollectFailureReplay):
                    raise BackendConflict(replay.message)
                return replay.receipt

            # Replay precedes refresh so retries cannot be replanned from later state.
            state = self._synchronize_session_instrument(
                session,
                runtime,
                instrument,
            )
            resolution = resolve_interactive_collect(
                intent=intent,
                description=instrument.description,
                state=state,
            )
            if isinstance(resolution, RejectedInteractiveCollect):
                receipt = CollectReceipt(
                    status="not_collected",
                    problems=resolution.problems,
                )
                ledger.remember(
                    intent.command_id,
                    CollectRejectionReplay(
                        intent=intent,
                        receipt=receipt,
                    ),
                )
                return receipt
            command = resolution.command
            return self._execute_interactive_collect(
                runtime,
                instrument,
                intent=intent,
                command=command,
                on_started=lambda: self._record_operation_started(
                    session,
                    instrument_id=instrument_id,
                    operation_id=intent.command_id,
                    kind="collect",
                ),
                on_finished=lambda status: self._record_operation_finished(
                    session,
                    instrument_id=instrument_id,
                    operation_id=intent.command_id,
                    kind="collect",
                    status=status,
                ),
                on_unknown=lambda reason: self._lose_runtime(
                    session,
                    runtime,
                    reason=reason,
                ),
            )

    def _execute_interactive_apply(
        self,
        runtime: OwnershipRuntime,
        instrument: OwnedInstrument,
        *,
        command: InstrumentStateCommand,
        on_started: Callable[[], None],
        on_finished: Callable[[str], None],
        on_unknown: Callable[[str], None],
    ) -> ApplyReceipt:
        command_id = command.command_id
        ledger = runtime.ledgers[instrument.instrument_id]
        driver_request = lower_backend_apply_request(command)
        on_started()
        try:
            receipt = execute_instrument_apply(
                instrument,
                driver_request,
                assignments=command.assignments,
            )
        except InstrumentCommandExecutionError as error:
            on_unknown(error.reason)
            raise BackendConflict(str(error)) from error
        ledger.remember(
            command_id,
            ApplyReplay(command=command, receipt=receipt),
        )
        try:
            on_finished(receipt.status)
        except Exception as error:
            on_unknown("instrument_apply_audit_unknown")
            raise BackendConflict(
                "instrument apply completed but audit recording failed"
            ) from error
        if receipt.status == "unknown":
            on_unknown("instrument_apply_receipt_unknown")
        return receipt

    def _invoke_live(
        self,
        runtime: OwnershipRuntime,
        instrument: OwnedInstrument,
        *,
        command: InvokeCommand,
        payload_scope: CommandPayloadScope,
        conflict_scope: str,
        on_started: Callable[[], None],
        on_finished: Callable[[str], None],
        on_unknown: Callable[[str], None],
    ) -> InvokeReceipt:
        command_id = command.command_id
        ledger = runtime.ledgers[instrument.instrument_id]
        replay = ledger.replay(command_id)
        if replay is not None and not isinstance(replay, InvokeReplay):
            raise BackendConflict(
                f"{conflict_scope} command id was already used for another command kind"
            )
        validation_problems = validate_invoke_command(
            command=command,
            description=instrument.description,
        )
        codec_issues = payload_codec_issues(
            command.payloads,
            runtime.payload_catalog,
        )
        if validation_problems or codec_issues:
            raise BackendConflict(
                "; ".join(
                    [
                        *(item.message for item in validation_problems),
                        *(message for _code, message in codec_issues),
                    ]
                )
            )
        canonical_command = self._payloads.canonicalize_invoke_command(
            command,
            scope=payload_scope,
        )
        if replay is not None:
            if replay.command != canonical_command:
                raise BackendConflict(
                    f"{conflict_scope} command id has different invoke content"
                )
            return replay.receipt
        backend_request = lower_backend_invoke_request(
            canonical_command,
            materialized_payloads=self._payloads.materialize_payloads(
                canonical_command.payloads,
                scope=payload_scope,
            ),
        )
        on_started()
        try:
            receipt = execute_instrument_invoke(instrument, backend_request)
        except InstrumentCommandExecutionError as error:
            on_unknown(error.reason)
            raise BackendConflict(str(error)) from error
        ledger.remember(
            command_id,
            InvokeReplay(
                command=canonical_command,
                receipt=receipt,
            ),
        )
        try:
            on_finished(receipt.status)
        except Exception as error:
            on_unknown("instrument_invoke_audit_unknown")
            raise BackendConflict(
                "instrument invoke completed but audit recording failed"
            ) from error
        if receipt.status == "unknown":
            on_unknown("instrument_invoke_receipt_unknown")
        return receipt

    def _execute_interactive_collect(
        self,
        runtime: OwnershipRuntime,
        instrument: OwnedInstrument,
        *,
        intent: InteractiveCollectIntent,
        command: CollectCommand,
        on_started: Callable[[], None],
        on_finished: Callable[[str], None],
        on_unknown: Callable[[str], None],
    ) -> CollectReceipt:
        command_id = intent.command_id
        ledger = runtime.ledgers[instrument.instrument_id]
        driver_request = lower_backend_collect_request(command)
        on_started()
        try:
            receipt = execute_instrument_collect(
                instrument,
                driver_request,
                command=command,
            )
        except InstrumentCommandExecutionError as error:
            if error.reason != "instrument_collect_receipt_invalid":
                on_unknown(error.reason)
                raise BackendConflict(str(error)) from error
            message = str(error)
            try:
                on_finished("invalid_receipt")
            except Exception as audit_error:
                on_unknown("instrument_collect_audit_unknown")
                raise BackendConflict(
                    "instrument collect completed but audit recording failed"
                ) from audit_error
            ledger.remember(
                command_id,
                CollectFailureReplay(
                    intent=intent,
                    command=command,
                    message=message,
                ),
            )
            raise BackendConflict(message) from error
        ledger.remember(
            command_id,
            CollectReceiptReplay(
                intent=intent,
                command=command,
                receipt=receipt,
            ),
        )
        try:
            on_finished(receipt.status)
        except Exception as error:
            on_unknown("instrument_collect_audit_unknown")
            raise BackendConflict(
                "instrument collect completed but audit recording failed"
            ) from error
        if receipt.status == "unknown":
            on_unknown("instrument_collect_receipt_unknown")
        return receipt

    def close_session(
        self,
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        return self._end_session(
            session_id,
            abort=False,
        )

    def abort_session(
        self,
        session_id: str,
    ) -> InstrumentSessionEndReceipt:
        return self._end_session(
            session_id,
            abort=True,
        )

    def resolve_attention(self, session_id: str) -> InstrumentSessionEndReceipt:
        with self._open_lock, self._attention_lock:
            try:
                session = self._control.get_instrument_session(session_id)
                if session.state == "attention_required":
                    self._cleanup_session_runtime(session_id)
                self._control.resolve_instrument_session_attention(session_id)
            except ControlPlaneNotFound as error:
                raise BackendNotFound(str(error)) from error
            except ControlPlaneConflict as error:
                raise BackendConflict(str(error)) from error
        return InstrumentSessionEndReceipt(
            session_id=session_id,
            status="aborted",
        )

    def _cleanup_session_runtime(self, session_id: str) -> None:
        with self._sessions_lock:
            context = self._sessions.get(session_id)
        if context is None:
            self._payloads.release_owner("session", session_id)
            return
        runtime = context.runtime
        with runtime.lock:
            with self._sessions_lock:
                if self._sessions.get(session_id) is not context:
                    return
            fault_ownership(runtime, abort=True)
            with self._sessions_lock:
                if self._sessions.get(session_id) is context:
                    self._sessions.pop(session_id)
            self._payloads.release_owner("session", session_id)

    def expire_leases(self, *, at: datetime | None = None) -> None:
        """Fence expired owners and finish their daemon-owned cleanup."""

        checked_at = at or datetime.now(tz=UTC)
        with self._attention_lock:
            self.expire_runs(self._control.expire_executor_leases(at=checked_at))
        with self._open_lock:
            expired_sessions = self._control.expired_instrument_sessions(at=checked_at)
        for session in expired_sessions:
            self._expire_session(session, checked_at=checked_at)

    def _expire_session(
        self,
        expired: InstrumentSession,
        *,
        checked_at: datetime,
    ) -> None:
        try:
            context = self._live_session_context(expired.session_id)
        except BackendConflict:
            with self._lifecycle_lock:
                if self._stopping:
                    return
                current = self._control.get_instrument_session(expired.session_id)
                if current.state != "active" or current.expires_at > checked_at:
                    return
                self._mark_unknown(
                    current,
                    reason="instrument_session_lease_runtime_missing",
                )
            return

        with context.lease_lock:
            with self._sessions_lock:
                if self._sessions.get(expired.session_id) is not context:
                    return
            runtime = context.runtime
            with runtime.lock:
                current = self._control.get_instrument_session(expired.session_id)
                if current.state != "active" or current.expires_at > checked_at:
                    return
                if current.active_operation_id is not None:
                    fault_ownership(runtime, abort=True)
                    self._pop_runtime(current.session_id)
                    self._mark_unknown(
                        current,
                        reason="instrument_session_lease_expired_during_operation",
                    )
                    return

                if release_instruments(runtime.instruments.values()):
                    fault_ownership(runtime, abort=False)
                    self._pop_runtime(current.session_id)
                    self._mark_unknown(
                        current,
                        reason="instrument_session_lease_release_failed",
                    )
                    return

                self._pop_runtime(current.session_id)
                try:
                    self._control.expire_instrument_session(
                        current.session_id,
                        at=checked_at,
                    )
                except Exception as error:
                    self._mark_unknown(
                        current,
                        reason="instrument_session_lease_close_failed",
                    )
                    raise BackendConflict(
                        "expired instrument ownership could not be released"
                    ) from error

    def finalize_run(self, run_id: str, *, token: str) -> None:
        """Release any instrument ownership left before a terminal commit."""

        context = self._run_context_state(run_id)
        if context is None:
            self._fence_run(run_id, token)
            return
        with context.lock:
            self._fence_run(run_id, token)
            if context.finalization is not None:
                return
            context.finalization = RunFinalizing()
            runtime = context.runtime
            if runtime is not None:
                self._finalize_run_instruments(
                    run_id,
                    token=token,
                    runtime=runtime,
                    baseline_state=(),
                    failed=True,
                    operation_id="hardware.terminal-fallback",
                )
            context.provision = None
            context.runtime = None

    def release_run(self, run_id: str) -> None:
        """Drop volatile idempotency state after the run is durably closed."""

        self._discard_run_state(run_id)

    def _discard_run_state(self, run_id: str) -> None:
        self._payloads.release_owner("run", run_id)
        with self._run_lock:
            context = self._run_contexts.get(run_id)
        if context is None:
            return
        with context.lock, self._run_lock:
            if self._run_contexts.get(run_id) is context:
                self._run_contexts.pop(run_id)

    def _run_context_state(self, run_id: str) -> RunContext | None:
        with self._run_lock:
            return self._run_contexts.get(run_id)

    def expire_runs(self, run_ids: Iterable[str]) -> None:
        """Release volatile instrument ownership after executor leases are fenced."""

        for run_id in run_ids:
            self._cleanup_run_state(run_id)

    def await_run_cleanup(self, run_id: str) -> None:
        """Complete hardware cleanup before quarantined resources are released."""

        try:
            run = self._control.get_run(run_id)
        except ControlPlaneNotFound:
            return
        if run.state == "attention_required":
            self._cleanup_run_state(run_id)

    def resolve_run_attention[T](
        self,
        run_id: str,
        resolver: Callable[[str], T],
    ) -> T:
        with self._attention_lock:
            self.await_run_cleanup(run_id)
            return resolver(run_id)

    def _cleanup_run_state(self, run_id: str) -> None:
        context = self._run_context_state(run_id)
        if context is None:
            return
        with context.lock:
            with self._run_lock:
                if self._run_contexts.get(run_id) is not context:
                    return
                self._run_contexts.pop(run_id)
                runtime = context.runtime
                context.runtime = None
            self._payloads.release_owner("run", run_id)
            if runtime is not None:
                with runtime.lock:
                    self._emergency_fault_ownership(
                        runtime,
                        operation_id="hardware.owner_loss",
                    )

    def reconcile_startup(self) -> None:
        with self._attention_lock:
            self.expire_runs(self._control.abandon_executor_leases())
            self._control.reconcile_instrument_sessions_after_restart()

    def _require_running(self) -> None:
        with self._lifecycle_lock:
            if self._stopping:
                raise BackendConflict("instrument service is shutting down")

    def shutdown(self) -> None:
        with self._shutdown_lock:
            self._shutdown()

    def _shutdown(self) -> None:
        # Fence acquisition before taking the owner snapshots. Otherwise a slow
        # connect can publish a durable owner after the shutdown drain has passed.
        with self._lifecycle_lock:
            if self._stopping:
                return
            self._stopping = True
            self._actors.stop_accepting()
            with self._sessions_lock:
                sessions = tuple(self._sessions.items())
                self._sessions.clear()
            with self._run_lock:
                run_contexts = tuple(self._run_contexts.items())
                self._run_contexts = {}
        endpoint = self._endpoint
        deadline = (
            None
            if endpoint is None
            else Timer(
                self._shutdown_grace_seconds,
                shutdown_endpoint,
                args=(endpoint,),
            )
        )
        if deadline is not None:
            # Worker termination is the last resort when driver code ignores abort.
            deadline.daemon = True
            deadline.start()
        try:
            self._drain_shutdown(
                sessions=sessions,
                run_contexts=run_contexts,
            )
        finally:
            if deadline is not None:
                deadline.cancel()
            if endpoint is not None:
                shutdown_endpoint(endpoint)

    def _drain_shutdown(
        self,
        *,
        sessions: tuple[tuple[str, SessionContext], ...],
        run_contexts: tuple[tuple[str, RunContext], ...],
    ) -> None:
        for session_id, context in sessions:
            runtime = context.runtime
            with context.lease_lock, runtime.lock:
                try:
                    session = self._control.get_instrument_session(session_id)
                except ControlPlaneNotFound:
                    continue
                if session.state != "active":
                    continue
                if session.active_operation_id is None:
                    failed = release_instruments(runtime.instruments.values())
                else:
                    failed = fault_ownership(runtime, abort=True)
                if failed or session.active_operation_id is not None:
                    self._mark_unknown(
                        session,
                        reason="instrument_shutdown_cleanup_unknown",
                    )
                else:
                    self._control.close_instrument_session(
                        session_id,
                        status="aborted",
                    )
        for run_id, context in run_contexts:
            with context.lock:
                provision = context.provision
                runtime = context.runtime
                if runtime is not None:
                    with runtime.lock:
                        self._emergency_fault_ownership(
                            runtime,
                            operation_id="hardware.daemon_shutdown",
                        )
                if provision is not None:
                    self._mark_run_unknown(
                        run_id,
                        token=provision.command.lease_id,
                        reason="daemon_shutting_down",
                    )
        with suppress(Exception):
            self._actors.shutdown()

    def _end_session(
        self,
        session_id: str,
        *,
        abort: bool,
    ) -> InstrumentSessionEndReceipt:
        try:
            session = self._control.get_instrument_session(session_id)
            if session.state == "closed":
                assert session.end_status is not None
                self._payloads.release_owner("session", session_id)
                return InstrumentSessionEndReceipt(
                    session_id=session_id,
                    status=session.end_status,
                )
            session = self._control.validate_instrument_session(session_id)
            runtime = self._live_runtime(session_id)
        except ControlPlaneNotFound as error:
            raise BackendNotFound(str(error)) from error
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error
        with runtime.lock:
            try:
                session = self._control.validate_instrument_session(session_id)
            except ControlPlaneConflict as error:
                raise BackendConflict(str(error)) from error
            failed = abort_instruments(runtime.instruments.values()) if abort else False
            if failed:
                self._lose_runtime(
                    session,
                    runtime,
                    reason="instrument_abort_unknown",
                    abort=False,
                )
                raise BackendConflict("instrument abort was not confirmed")
            if release_instruments(runtime.instruments.values()):
                self._pop_runtime(session.session_id)
                self._mark_unknown(
                    session,
                    reason="instrument_ownership_release_failed",
                )
                raise BackendConflict("instrument ownership release failed")
            try:
                self._control.close_instrument_session(
                    session_id,
                    status="aborted" if abort else "closed",
                )
            except ControlPlaneConflict as error:
                self._pop_runtime(session.session_id)
                self._mark_unknown(
                    session,
                    reason="instrument_session_close_failed",
                )
                raise BackendConflict(str(error)) from error
            self._pop_runtime(session_id)
            return InstrumentSessionEndReceipt(
                session_id=session_id,
                status="aborted" if abort else "closed",
            )

    def _session_instrument(
        self,
        session_id: str,
        instrument_id: str,
    ) -> tuple[InstrumentSession, OwnershipRuntime, OwnedInstrument]:
        try:
            session = self._control.validate_instrument_session(session_id)
            runtime = self._live_runtime(session_id)
        except ControlPlaneNotFound as error:
            raise BackendNotFound(str(error)) from error
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error
        try:
            instrument = runtime.instruments[instrument_id]
        except KeyError as error:
            raise BackendNotFound(
                f"instrument is not in session {session_id}: {instrument_id}"
            ) from error
        return session, runtime, instrument

    def _live_runtime(self, session_id: str) -> OwnershipRuntime:
        return self._live_session_context(session_id).runtime

    def _live_session_context(self, session_id: str) -> SessionContext:
        with self._sessions_lock:
            context = self._sessions.get(session_id)
        if context is None:
            raise BackendConflict("instrument session has no owned daemon instruments")
        return context

    def _pop_runtime(self, session_id: str) -> OwnershipRuntime | None:
        with self._sessions_lock:
            context = self._sessions.pop(session_id, None)
        self._payloads.release_owner("session", session_id)
        return None if context is None else context.runtime

    def _lose_runtime(
        self,
        session: InstrumentSession,
        runtime: OwnershipRuntime,
        *,
        reason: str,
        abort: bool = True,
    ) -> None:
        fault_ownership(runtime, abort=abort)
        self._pop_runtime(session.session_id)
        self._mark_unknown(session, reason=reason)

    def _end_failed_observation(
        self,
        session: InstrumentSession,
        runtime: OwnershipRuntime,
    ) -> None:
        """Release a read-only failure without claiming hardware was modified."""

        fault_ownership(runtime, abort=False)
        self._pop_runtime(session.session_id)
        try:
            self._control.close_instrument_session(
                session.session_id,
                status="aborted",
            )
        except Exception as error:
            self._mark_unknown(
                session,
                reason="instrument_session_observation_cleanup_failed",
            )
            raise BackendConflict(
                "instrument session observation failure could not be released"
            ) from error

    def _lose_run_runtime(
        self,
        run_id: str,
        runtime: OwnershipRuntime,
        *,
        token: str,
        reason: str,
        abort: bool = True,
    ) -> None:
        failures: list[tuple[OwnedInstrument, str, Exception]] = []
        fault_ownership(runtime, abort=abort, failures=failures)
        try:
            for instrument, operation, error in failures:
                self._record_cleanup_failure(
                    run_id,
                    token=token,
                    instrument_id=instrument.instrument_id,
                    operation=operation,
                    operation_id=f"hardware.fault.{operation}.{instrument.instrument_id}",
                    error=error,
                )
        finally:
            self._pop_run_runtime(run_id, expected=runtime)
            self._mark_run_unknown(run_id, token=token, reason=reason)

    def _record_cleanup_failure(
        self,
        run_id: str,
        *,
        token: str,
        instrument_id: str,
        operation: str,
        operation_id: str,
        error: Exception,
    ) -> None:
        cleanup_problem = hardware_problem(
            f"run_instrument_{operation}_unknown",
            f"Instrument {operation} failed with unknown state",
            run_id=run_id,
            operation_id=operation_id,
            instrument_id=instrument_id,
        )
        if isinstance(error, InstrumentBackendError) and error.diagnostic is not None:
            cleanup_problem = cleanup_problem.model_copy(
                update={"details": {"worker_diagnostic": error.diagnostic}}
            )
        self._record_run_operation_event(
            run_id,
            token=token,
            instrument_id=instrument_id,
            operation_id=operation_id,
            event_kind="run_hardware_finalization_failed",
            status="unknown",
            details={"problems": [cleanup_problem.model_dump(mode="json")]},
        )

    def _mark_unknown(
        self,
        session: InstrumentSession,
        *,
        reason: str,
    ) -> None:
        with suppress(InstrumentSessionNotActive):
            self._control.mark_instrument_session_unknown(
                session.session_id,
                reason=reason,
            )

    def _record_operation_started(
        self,
        session: InstrumentSession,
        *,
        instrument_id: str,
        operation_id: str,
        kind: Literal["apply", "invoke", "collect"],
    ) -> None:
        try:
            self._control.start_instrument_operation(
                session.session_id,
                instrument_id=instrument_id,
                operation_id=operation_id,
                kind=kind,
            )
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error

    def _record_operation_finished(
        self,
        session: InstrumentSession,
        *,
        instrument_id: str,
        operation_id: str,
        kind: Literal["apply", "invoke", "collect"],
        status: str,
    ) -> None:
        try:
            self._control.finish_instrument_operation(
                session.session_id,
                instrument_id=instrument_id,
                operation_id=operation_id,
                kind=kind,
                status=status,
            )
        except ControlPlaneConflict as error:
            raise BackendConflict(str(error)) from error

    def _fence_run(self, run_id: str, token: str) -> None:
        try:
            self._control.validate_executor_lease(run_id, token=token)
        except ExecutorLeaseNotHeld as error:
            raise BackendConflict(
                "executor lease is absent, stale, or expired"
            ) from error

    def _mark_run_unknown(
        self,
        run_id: str,
        *,
        token: str,
        reason: str,
    ) -> None:
        try:
            with suppress(ExecutorLeaseNotHeld):
                self._control.mark_executor_unknown(
                    run_id,
                    token=token,
                    reason=reason,
                )
        finally:
            self._discard_run_state(run_id)

    def _record_run_operation_event(
        self,
        run_id: str,
        *,
        token: str,
        instrument_id: str | None,
        operation_id: str,
        event_kind: str,
        status: str | None,
        details: Mapping[str, JsonValue] | None = None,
    ) -> None:
        payload: dict[str, JsonValue] = {"operation_id": operation_id}
        if instrument_id is not None:
            payload["instrument_id"] = instrument_id
        if status is not None:
            payload["status"] = status
        payload.update(details or {})
        try:
            with self._control.fenced_transaction(
                run_id,
                token=token,
            ) as connection:
                self._control.append_event_in_transaction(
                    connection,
                    DurableEventInput(
                        run_id=run_id,
                        kind=event_kind,
                        payload=payload,
                    ),
                )
        except (ControlPlaneConflict, ExecutorLeaseNotHeld) as error:
            raise BackendConflict(
                "executor lease is absent, stale, or expired"
            ) from error
        except Exception as error:
            raise BackendConflict(
                "run instrument audit event could not be recorded"
            ) from error

    def _selected_descriptions(
        self,
        catalog: InstrumentContractCatalog,
        *,
        config: ConfigProfileSnapshot,
        instrument_ids: tuple[str, ...],
    ) -> dict[str, InstrumentDescription]:
        global_problems, instrument_problems = scope_provider_problems(
            config.instrument_registry.instruments,
            catalog.problems,
        )
        selected_problems = (
            *global_problems,
            *(
                item
                for instrument_id in instrument_ids
                for item in instrument_problems.get(instrument_id, ())
            ),
        )
        if selected_problems:
            raise BackendConflict("instrument provider cannot describe the session")
        descriptions = {
            description.instrument_id: description
            for description in catalog.instruments
            if description.instrument_id in instrument_ids
        }
        missing = tuple(
            instrument_id
            for instrument_id in instrument_ids
            if instrument_id not in descriptions
        )
        if missing:
            raise BackendConflict(
                f"instrument provider does not expose: {', '.join(missing)}"
            )
        return descriptions

    @staticmethod
    def _require_supported_binding(
        endpoint: InstrumentBackendEndpoint,
        binding: InstrumentBindingSpec,
    ) -> None:
        registered = endpoint.driver_catalog.get(binding.driver_id)
        if registered is None:
            raise BackendNotFound(
                f"instrument driver was not found: {binding.driver_id}"
            )
        connection_kind = binding.connection.kind
        if all(
            connection.kind != connection_kind for connection in registered.connections
        ):
            raise BackendConflict(
                f"{binding.driver_id} does not support {connection_kind} connections"
            )

    @staticmethod
    def _describe_temporary_bindings(
        endpoint: InstrumentBackendEndpoint,
        *,
        bindings: tuple[InstrumentBindingSpec, ...],
    ) -> dict[str, InstrumentDescription]:
        try:
            advertised = endpoint.describe(bindings)
        except InstrumentBackendUnavailable as error:
            raise BackendConflict(
                "instrument provider cannot describe temporary bindings"
            ) from error
        if advertised.provider_id != endpoint.provider_id or advertised.problems:
            raise BackendConflict(
                "instrument provider cannot describe temporary bindings"
            )
        descriptions = {
            description.instrument_id: description
            for description in advertised.instruments
        }
        requested_ids = tuple(binding.id for binding in bindings)
        if set(descriptions) != set(requested_ids):
            raise BackendConflict(
                "instrument provider description does not match temporary bindings"
            )
        return descriptions

    @staticmethod
    def _temporary_instrument_spec(
        binding: InstrumentBindingSpec,
        *,
        configured: tuple[InstrumentSpec, ...],
    ) -> InstrumentSpec:
        matching = next(
            (
                spec
                for spec in configured
                if spec.driver_id == binding.driver_id
                and spec.connection == binding.connection
            ),
            None,
        )
        access_key = (
            matching.exclusivity_key
            if matching is not None
            else "temporary:"
            + stable_content_hash(
                {
                    "driver_id": binding.driver_id,
                    "connection": binding.connection.model_dump(mode="json"),
                }
            )
        )
        return InstrumentSpec(
            id=binding.id,
            exclusivity_key=access_key,
            driver_id=binding.driver_id,
            connection=binding.connection.model_copy(deep=True),
            run_start="preserve",
            success_action="release",
            failure_action="abort_and_release",
        )

    def _wire_session(
        self,
        session: InstrumentSession,
        runtime: OwnershipRuntime,
    ) -> InstrumentSessionOpenReceipt:
        configured = {spec.id for spec in runtime.specs.values() if spec.default_state}
        return InstrumentSessionOpenReceipt(
            session_id=session.session_id,
            actor=session.actor,
            config_entry_id=session.config_entry_id,
            config_content_hash=session.config_content_hash,
            instrument_ids=session.instrument_ids,
            configured_default_instrument_ids=tuple(
                instrument_id
                for instrument_id in session.instrument_ids
                if instrument_id in configured
            ),
            descriptions=tuple(
                runtime.instruments[instrument_id].description
                for instrument_id in session.instrument_ids
            ),
            observed_state=tuple(
                state.model_copy(deep=True) for state in runtime.opening_state
            ),
            opened_at=session.acquired_at,
            renewed_at=session.renewed_at,
            expires_at=session.expires_at,
        )

    @staticmethod
    def _wire_session_lease(
        session: InstrumentSession,
    ) -> InstrumentSessionLeaseReceipt:
        return InstrumentSessionLeaseReceipt(
            session_id=session.session_id,
            renewed_at=session.renewed_at,
            expires_at=session.expires_at,
        )

    def _pinned_session_config(
        self,
        session: InstrumentSession,
    ) -> ConfigProfileSnapshot:
        pinned = self._config.get_config_entry(session.config_entry_id)
        if pinned.entry.content_hash != session.config_content_hash:
            raise BackendConflict(
                "instrument session pinned config content does not match its entry"
            )
        return pinned.config

    @staticmethod
    def _instrument_view(
        spec: InstrumentSpec,
        *,
        description: InstrumentDescription | None,
        claim: ResourceClaim | None,
        owner_actor: str | None,
        problems: tuple[Problem, ...],
    ) -> InstrumentView:
        if claim is not None:
            availability = claim.status
        elif description is None:
            availability = "unavailable"
        else:
            availability = "available"
        return InstrumentView(
            instrument_id=spec.id,
            driver_id=spec.driver_id,
            connection=instrument_connection_summary(spec.connection),
            description=description,
            availability=availability,
            owner_kind=None if claim is None else claim.owner_kind,
            owner_id=None if claim is None else claim.owner_id,
            owner_actor=owner_actor,
            problems=problems,
        )


__all__ = ["InstrumentRuntime"]
