"""Notebook-facing execution of durable multi-run procedures."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Literal, Protocol
from uuid import uuid4

import httpx2
from pydantic import BaseModel, JsonValue, ValidationError

from scopecat.analysis.facts import AnalysisFactSchema
from scopecat.api._config import LabConfigOperations
from scopecat.api._runner import (
    RunResourcesBlocked,
    _DaemonRunner,
    _prepare_run_submission,
)
from scopecat.api.analysis import AnalysisInvocation, AnalysisStep
from scopecat.api.procedure_planner import (
    ProcedurePlanningConfig,
    ProcedurePlanningContext,
    ProjectProcedureIntervalPlanner,
)
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.authoring.experiments import Experiment, ExperimentInvocation
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ConfigActivationOutputRef,
    ConfigPublishOutputRef,
    InterpretationActorKind,
    InterpretationOutputRef,
    InterpretationRequest,
    ProcedureCancelCommand,
    ProcedureContext,
    ProcedureRegistry,
    ProcedureRun,
    ProcedureRunListQuery,
    ProcedureRunnablePage,
    ProcedureRunnableQuery,
    ProcedureRunState,
    ProcedureSchedule,
    ProcedureScheduleCancelCommand,
    ProcedureScheduleCreateCommand,
    ProcedureScheduleDuePage,
    ProcedureScheduleDueQuery,
    ProcedureScheduleListQuery,
    ProcedureScheduleMaterializeCommand,
    ProcedureSchedulePage,
    ProcedureScheduleRegistry,
    ProcedureScheduleState,
    ProcedureStepAttempt,
    ProcedureStepAttemptListQuery,
    ProcedureStepAttemptPage,
    ProcedureStepAttentionRetryCommand,
    ProcedureStepInputSubmitCommand,
    ProcedureStepOperation,
    ProcedureStepOutputRef,
    ProcedureSubmitCommand,
    ProcedureWorker,
    RegisteredProcedure,
    RunOutputRef,
)
from scopecat.automation.recovery import (
    ProcedureRecoveryAdapter,
    ProcedureRecoveryAvailability,
    ProcedureRecoveryPlan,
)
from scopecat.automation.worker import ProcedureNeedsAttention, ProcedureWaitResources
from scopecat.config.candidates import CandidateConfig
from scopecat.config.registry.records import (
    CandidateConfigRegistrySource,
    CrossRunCandidateAcceptance,
)
from scopecat.daemon.client import (
    DaemonClient,
    DaemonClientError,
    DaemonNotFoundError,
    DaemonUnavailableError,
)
from scopecat.daemon.wire import (
    CandidateConfigRevisionSource,
    ConfigActivationReceipt,
    ConfigEntryActivationCommand,
    ConfigPublishCommand,
    ConfigPublishReceipt,
)
from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash
from scopecat.kernel.errors import RunIndeterminate
from scopecat.kernel.ids import artifact_slug
from scopecat.kernel.python_source import python_source_identity
from scopecat.kernel.run_outcome import utc_now
from scopecat.program.values import MetadataValue
from scopecat.records.analysis import (
    InterpretationAnalysisRecordInput,
    MeasurementAnalysisRecordInput,
    ProjectAnalysisDecisionReference,
    ProjectAnalysisSubject,
    RunAnalysisSubject,
    SampleAnalysisSubject,
    analysis_record_id,
)
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash
from scopecat.records.content import Sha256ContentHash
from scopecat.records.manual_preview import ManualPreviewFence
from scopecat.records.parameter_change import ParameterChangeProposal
from scopecat.records.run import RunConfigSource
from scopecat.records.sample import SampleSelector
from scopecat.runs.selectors import RunSelector

type ExperimentSpec = ExperimentInvocation | Experiment[...]

_ANALYSIS_STEP_INTENT_CODEC = "scopecat.procedure-analysis-step.v1"
_VERIFIED_CANDIDATE_PUBLISH_STEP_INTENT_CODEC = (
    "scopecat.procedure-verified-candidate-publish-step.v1"
)


class ProcedureLabSession(Protocol):
    """Notebook operations required by a lab-bound procedure context."""

    def get_run(self, run: RunSelector | RunHandle) -> RunHandle: ...

    def analyze(
        self,
        step: AnalysisStep,
        *,
        key: str | None = None,
    ) -> PublishedAnalysis: ...

    def published_analysis(
        self,
        selector: str,
        *,
        sample: str | None = None,
    ) -> PublishedAnalysis: ...


@dataclass(frozen=True, slots=True)
class ProcedureHandlePage:
    """One bounded newest-first page of notebook procedure handles."""

    items: tuple[ProcedureHandle, ...] = ()
    next_cursor: int | None = None


@dataclass(frozen=True, slots=True)
class InterpretationResult[ValueT]:
    """Typed judgment together with its exact durable procedure reference."""

    value: ValueT
    ref: InterpretationOutputRef


@dataclass(frozen=True, slots=True)
class ProcedureSummary:
    """Bounded observational view, not an atomic authorization to mutate a run."""

    procedure_run_id: str
    revision: int
    outcome: str
    next_action: Literal["respond", "resume", "inspect_attention", "wait", "none"]
    reason: str | None
    steps: tuple[ProcedureStepAttempt, ...]
    next_cursor: int | None


@dataclass(frozen=True, slots=True)
class ProcedureHandle:
    """Reopenable handle for one daemon-owned procedure invocation."""

    operations: LabProcedureOperations
    id: str

    @property
    def snapshot(self) -> ProcedureRun:
        return self.operations.snapshot(self.id)

    @property
    def state(self) -> ProcedureRunState:
        return self.snapshot.state

    def steps(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
    ) -> ProcedureStepAttemptPage:
        return self.operations.steps(self.id, limit=limit, before=before)

    def summary(self, *, limit: int = 20) -> ProcedureSummary:
        """Read one snapshot and one bounded newest-first step page.

        Steps retain typed requests and exact evidence references. A cursor means
        older attempts are omitted; this method never computes totals by scanning.
        Concurrent workers may advance the run between the two reads.
        """
        snapshot = self.snapshot
        page = self.steps(limit=limit)
        actions: dict[
            ProcedureRunState,
            Literal["respond", "resume", "inspect_attention", "wait", "none"],
        ] = {
            "waiting_for_input": "respond",
            "ready": "resume",
            "attention_required": "inspect_attention",
            "leased": "wait",
            "closed": "none",
        }
        return ProcedureSummary(
            procedure_run_id=self.id,
            revision=snapshot.revision,
            outcome=snapshot.closure.status if snapshot.closure else snapshot.state,
            next_action="wait"
            if snapshot.resource_wait is not None and snapshot.state == "ready"
            else actions[snapshot.state],
            reason=snapshot.closure.reason
            if snapshot.closure
            else snapshot.attention_reason,
            steps=page.items,
            next_cursor=page.next_cursor,
        )

    def step(self, step_key: str) -> ProcedureStepAttempt:
        """Find the newest attempt for one stable step using bounded pages."""

        cursor: int | None = None
        while True:
            page = self.steps(limit=200, before=cursor)
            for attempt in page.items:
                if attempt.step_key == step_key:
                    return attempt
            if page.next_cursor is None:
                raise KeyError(f"procedure has no step {step_key!r}")
            cursor = page.next_cursor

    def output(self, step_key: str) -> ProcedureStepOutputRef:
        attempt = self.step(step_key)
        if attempt.state != "succeeded" or attempt.output is None:
            raise RuntimeError(f"procedure step {step_key!r} has no successful output")
        return attempt.output

    def cancel(self, *, actor: str, reason: str) -> ProcedureHandle:
        """Cancel idle work or stop after the current step settles."""
        return self.operations.cancel(self, actor=actor, reason=reason)

    def resume(self, *, worker_id: str | None = None) -> ProcedureHandle:
        return self.operations.resume(self, worker_id=worker_id)

    def retry_attention(self, *, worker_id: str | None = None) -> ProcedureHandle:
        """Authorize and execute a new attempt of this procedure's blocked step."""

        return self.operations.retry_attention(self, worker_id=worker_id)

    def respond[ValueT](
        self,
        step_key: str,
        value: ValueT,
        *,
        schema: AnalysisFactSchema[ValueT],
        actor: str,
        actor_kind: InterpretationActorKind = "human",
        note: str = "",
    ) -> ProcedureHandle:
        """Submit one typed judgment; call ``resume`` explicitly to continue."""

        return self.operations.respond(
            self,
            step_key,
            value,
            schema=schema,
            actor=actor,
            actor_kind=actor_kind,
            note=note,
        )


class LabProcedureContext:
    """Lab effects layered over one lease-fenced durable procedure context."""

    __slots__ = ("_config", "_durable", "_runner", "_session")

    def __init__(
        self,
        durable: ProcedureContext,
        *,
        runner: _DaemonRunner,
        config: LabConfigOperations,
        session: ProcedureLabSession,
    ) -> None:
        self._durable = durable
        self._runner = runner
        self._config = config
        self._session = session

    @property
    def procedure_run_id(self) -> str:
        return self._durable.procedure_run_id

    def step[OutputT: ProcedureStepOutputRef](
        self,
        step_key: str,
        *,
        operation: ProcedureStepOperation,
        intent_hash: Sha256ContentHash,
        effect: Callable[[str], OutputT],
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> OutputT:
        """Use the domain-neutral checkpoint primitive for a custom effect."""

        return self._durable.step(
            step_key,
            operation=operation,
            intent_hash=intent_hash,
            effect=effect,
            inputs=inputs,
        )

    def interpret[ValueT](
        self,
        step_key: str,
        *,
        title: str,
        instructions: str,
        schema: AnalysisFactSchema[ValueT],
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
        response_template: ValueT | None = None,
        metadata: Mapping[str, JsonValue] | None = None,
    ) -> InterpretationResult[ValueT]:
        """Wait for and replay a schema-validated human, AI, or service judgment."""

        request = InterpretationRequest(
            title=title,
            instructions=instructions,
            schema_id=schema.id,
            schema_codec=schema.schema_codec,
            schema_hash=schema.schema_hash,
            structure=schema.structure,
            response_template=(
                None if response_template is None else schema.encode(response_template)
            ),
            metadata={} if metadata is None else dict(metadata),
        )
        ref = self._durable.interpret(
            step_key,
            request=request,
            inputs=inputs,
        )
        return InterpretationResult(
            value=schema.decode(ref.response.value),
            ref=ref,
        )

    def run(
        self,
        step_key: str,
        experiment: ExperimentSpec,
        *,
        config: ConfigProfileSnapshot | CandidateConfig,
        config_source: RunConfigSource | None = None,
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        sample: str | SampleSelector | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> RunOutputRef:
        """Plan and execute one exactly identified child run."""

        selected_config, inferred_source = self._config.resolve_with_source(config)
        if inferred_source is not None and config_source is not None:
            raise TypeError(
                "config_source cannot override provenance inferred from a candidate"
            )
        selected_source = inferred_source or config_source
        if (
            selected_source is not None
            and selected_source.content_hash != config_content_hash(selected_config)
        ):
            raise ValueError("config_source content hash does not match config")
        invocation = _experiment_invocation(experiment)
        planned = self._runner._plan(  # pyright: ignore[reportPrivateUsage]
            invocation,
            config=selected_config,
            config_source=selected_source,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            samples=(
                self._durable.samples
                if sample is None and not samples
                else _procedure_sample_selectors(sample, samples)
            ),
        )
        _, intent_hash = _prepare_run_submission(
            planned,
            submission_id="procedure-step-intent",
        )

        def execute(operation_id: str) -> RunOutputRef:
            try:
                snapshot = self._runner.execute(
                    planned,
                    submission_id=operation_id,
                    executor_id=operation_id,
                    wait_for_resources=True,
                )
            except RunResourcesBlocked as error:
                raise ProcedureWaitResources(error.run_id) from error
            except httpx2.TransportError as error:
                raise ProcedureNeedsAttention(
                    "child run transport outcome for operation "
                    f"{operation_id!r} is unknown"
                ) from error
            except RunIndeterminate as error:
                raise ProcedureNeedsAttention(
                    f"child run {error.run_id!r} has an indeterminate outcome"
                ) from error
            return RunOutputRef(run_id=snapshot.run_id)

        return self.step(
            step_key,
            operation="run",
            intent_hash=intent_hash,
            effect=execute,
            inputs=inputs,
        )

    def run_handle(self, ref: RunOutputRef) -> RunHandle:
        """Rehydrate an exact durable child-run reference."""

        return self._session.get_run(ref.run_id)

    def analyze_run(
        self,
        step_key: str,
        run: RunOutputRef,
        analysis: AnalysisStep,
        *,
        inputs: tuple[ProcedureStepOutputRef, ...] | None = None,
    ) -> AnalysisPublicationOutputRef:
        """Publish or replay one analysis whose subject is an exact child run."""

        selected_inputs = (run,) if inputs is None else inputs
        if run not in selected_inputs:
            raise ValueError("run analysis inputs must include its subject run")
        key = _procedure_analysis_key(self.procedure_run_id, step_key)
        intent_hash = _analysis_step_intent_hash(
            procedure_run_id=self.procedure_run_id,
            step_key=step_key,
            analysis=analysis,
            key=key,
            inputs=selected_inputs,
        )
        handle = self.run_handle(run)

        def publish(_operation_id: str) -> AnalysisPublicationOutputRef:
            published = _existing_run_analysis(handle, key=key)
            if published is None:
                try:
                    published = handle.analyze(analysis, key=key)
                except httpx2.TransportError as error:
                    published = _existing_run_analysis(handle, key=key)
                    if published is None:
                        raise ProcedureNeedsAttention(
                            f"run analysis publication {key!r} has an unknown outcome"
                        ) from error
            _validate_run_analysis(
                published,
                run_id=run.run_id,
                step_id=analysis.id,
                key=key,
                inputs=selected_inputs,
            )
            return AnalysisPublicationOutputRef(
                subject=RunAnalysisSubject(run_id=run.run_id),
                analysis_record_id=published.id,
            )

        return self.step(
            step_key,
            operation="analysis",
            intent_hash=intent_hash,
            effect=publish,
            inputs=selected_inputs,
        )

    def analyze_project(
        self,
        step_key: str,
        analysis: AnalysisStep,
        *,
        inputs: tuple[ProcedureStepOutputRef, ...],
    ) -> AnalysisPublicationOutputRef:
        """Publish or replay one project analysis over exact durable inputs."""

        key = _procedure_analysis_key(self.procedure_run_id, step_key)
        intent_hash = _analysis_step_intent_hash(
            procedure_run_id=self.procedure_run_id,
            step_key=step_key,
            analysis=analysis,
            key=key,
            inputs=inputs,
        )

        def publish(_operation_id: str) -> AnalysisPublicationOutputRef:
            published = _existing_project_analysis(self._session, key=key)
            if published is None:
                try:
                    published = self._session.analyze(analysis, key=key)
                except httpx2.TransportError as error:
                    published = _existing_project_analysis(self._session, key=key)
                    if published is None:
                        raise ProcedureNeedsAttention(
                            "project analysis publication "
                            f"{key!r} has an unknown outcome"
                        ) from error
            _validate_project_analysis(
                published,
                step_id=analysis.id,
                key=key,
                inputs=inputs,
            )
            return AnalysisPublicationOutputRef(
                subject=ProjectAnalysisSubject(),
                analysis_record_id=published.id,
            )

        return self.step(
            step_key,
            operation="analysis",
            intent_hash=intent_hash,
            effect=publish,
            inputs=inputs,
        )

    def published_analysis(
        self,
        ref: AnalysisPublicationOutputRef,
    ) -> PublishedAnalysis:
        """Open one exact publication without resolving a mutable logical key."""

        try:
            if isinstance(ref.subject, RunAnalysisSubject):
                published = self._session.get_run(
                    ref.subject.run_id
                ).published_analysis(ref.analysis_record_id)
            elif isinstance(ref.subject, SampleAnalysisSubject):
                published = self._session.published_analysis(
                    ref.analysis_record_id,
                    sample=ref.subject.sample_id,
                )
            else:
                published = self._session.published_analysis(ref.analysis_record_id)
        except (
            httpx2.HTTPError,
            DaemonUnavailableError,
            ValidationError,
        ) as error:
            raise ProcedureNeedsAttention(
                f"analysis publication {ref.analysis_record_id!r} "
                "could not be reopened exactly"
            ) from error
        _validate_exact_analysis_ref(published, ref)
        return published

    def activate_config_entry(
        self,
        step_key: str,
        entry_id: str,
        *,
        expected_generation: int,
        actor: str,
        note: str = "",
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
    ) -> ConfigActivationOutputRef:
        """Activate one saved entry through an exact replayable command."""

        intent = ConfigEntryActivationCommand(
            operation_id="procedure-step:validation",
            entry_id=entry_id,
            expected_generation=expected_generation,
            actor=actor,
            note=note,
        )
        intent_hash = intent.intent_hash

        def activate(operation_id: str) -> ConfigActivationOutputRef:
            receipt = _execute_replayable_config_operation(
                operation_id=operation_id,
                operation_name="config activation",
                execute=lambda: self._config.activate_entry(
                    intent.entry_id,
                    operation_id=operation_id,
                    expected_generation=intent.expected_generation,
                    actor=intent.actor,
                    note=intent.note,
                ),
                lookup=self._config.activation_operation,
                validate=lambda candidate: _validate_config_activation_receipt(
                    candidate,
                    operation_id=operation_id,
                    intent_hash=intent_hash,
                    entry_id=intent.entry_id,
                    expected_generation=intent.expected_generation,
                    actor=intent.actor,
                    note=intent.note,
                ),
            )
            return ConfigActivationOutputRef(
                generation=receipt.activation.generation,
                entry_id=receipt.activation.entry_id,
                entry_content_hash=receipt.activation.entry_content_hash,
            )

        return self.step(
            step_key,
            operation="config_activation",
            intent_hash=intent_hash,
            effect=activate,
            inputs=inputs,
        )

    def accept_verified_candidate(
        self,
        step_key: str,
        candidate_ref: AnalysisPublicationOutputRef,
        *,
        proposal_id: str,
        verification: AnalysisPublicationOutputRef,
        decision_output_id: str,
        expected_generation: int,
        actor: str,
        entry_id: str,
        note: str = "",
    ) -> ConfigPublishOutputRef:
        """Publish and activate one candidate backed by an exact positive decision."""

        if not isinstance(candidate_ref.subject, RunAnalysisSubject):
            raise TypeError("candidate must identify an exact run analysis")
        candidate_run_id = candidate_ref.subject.run_id
        if not isinstance(verification.subject, ProjectAnalysisSubject):
            raise TypeError("candidate verification must identify a project analysis")
        intent_hash = _verified_candidate_publish_step_intent_hash(
            procedure_run_id=self.procedure_run_id,
            step_key=step_key,
            candidate_ref=candidate_ref,
            proposal_id=proposal_id,
            verification=verification,
            decision_output_id=decision_output_id,
            expected_generation=expected_generation,
            actor=actor,
            entry_id=entry_id,
            note=note,
        )

        def publish(operation_id: str) -> ConfigPublishOutputRef:
            candidate_analysis = self.published_analysis(candidate_ref)
            verification_analysis = self.published_analysis(verification)
            proposal = _parameter_proposal(candidate_analysis, proposal_id)
            if (
                proposal.source_run_id != candidate_run_id
                or proposal.analysis_record_id != candidate_ref.analysis_record_id
            ):
                raise ValueError(
                    "candidate proposal does not belong to its exact analysis reference"
                )
            decision = verification_analysis.fact(decision_output_id)
            if (
                not isinstance(decision.value, dict)
                or decision.value.get("accepted") is not True
            ):
                raise ValueError(
                    "candidate verification decision must contain accepted=true"
                )
            command = ConfigPublishCommand(
                operation_id=operation_id,
                source=CandidateConfigRevisionSource(
                    run_id=proposal.source_run_id,
                    proposal_id=proposal.id,
                    acceptance=CrossRunCandidateAcceptance(
                        decision=ProjectAnalysisDecisionReference(
                            analysis_record_id=verification.analysis_record_id,
                            output_id=decision_output_id,
                            schema_id=decision.schema_id,
                            schema_hash=decision.schema_hash,
                        )
                    ),
                ),
                expected_generation=expected_generation,
                actor=actor,
                entry_id=entry_id,
                note=note,
            )
            receipt = _execute_replayable_config_operation(
                operation_id=operation_id,
                operation_name="config publish",
                execute=lambda: self._config.publish_config(command),
                lookup=self._config.publish_operation,
                validate=lambda candidate: _validate_config_publish_receipt(
                    candidate,
                    command=command,
                    proposal_base_config_content_hash=(
                        proposal.base_config_content_hash
                    ),
                ),
            )
            return ConfigPublishOutputRef(
                generation=receipt.activation.generation,
                entry_id=receipt.entry.id,
                entry_content_hash=receipt.entry.content_hash,
            )

        return self.step(
            step_key,
            operation="config_publish",
            intent_hash=intent_hash,
            effect=publish,
            inputs=(candidate_ref, verification),
        )


class LabProcedureOperations:
    """Submit, execute, inspect, and resume registered lab procedures."""

    __slots__ = (
        "_client",
        "_config",
        "_registry",
        "_runner",
        "_schedule_registry",
        "_session",
        "_worker_id",
    )

    def __init__(
        self,
        *,
        client: DaemonClient,
        runner: _DaemonRunner,
        config: LabConfigOperations,
        session: ProcedureLabSession,
        registry: ProcedureRegistry,
        schedule_registry: ProcedureScheduleRegistry[ProcedurePlanningContext],
        worker_id: str | None = None,
    ) -> None:
        self._client = client
        self._runner = runner
        self._config = config
        self._session = session
        self._registry = registry
        for schedule in schedule_registry.values():
            registry.resolve(schedule.procedure.ref)
        self._schedule_registry = schedule_registry
        self._worker_id = worker_id or f"notebook-procedure-{uuid4().hex}"

    @property
    def registry(self) -> ProcedureRegistry:
        return self._registry

    @property
    def schedule_registry(
        self,
    ) -> ProcedureScheduleRegistry[ProcedurePlanningContext]:
        return self._schedule_registry

    @property
    def planning_context(self) -> ProcedurePlanningContext:
        return ProcedurePlanningContext(config=ProcedurePlanningConfig(self._config))

    def interval_planner(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> ProjectProcedureIntervalPlanner:
        """Build the project-side planner over this exact application registry."""

        return ProjectProcedureIntervalPlanner(
            self,
            self._schedule_registry,
            self.planning_context,
            clock=clock,
        )

    def submit(
        self,
        definition: RegisteredProcedure,
        intent: object,
        *,
        request_key: str,
        expected_manual_preview: ManualPreviewFence | None = None,
        expected_config_generation: int | None = None,
        sample: str | SampleSelector | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ProcedureHandle:
        """Admit durable intent, optionally fencing new work to a config generation.

        An exact request-key retry returns its existing procedure even if the
        active configuration has since changed.
        """
        selected = self._registry.resolve(definition.ref)
        receipt = self._client.submit_procedure(
            ProcedureSubmitCommand(
                request_key=request_key,
                expected_manual_preview=expected_manual_preview,
                expected_config_generation=expected_config_generation,
                definition=selected.ref,
                intent=selected.encode_intent(intent),
                samples=_procedure_sample_selectors(sample, samples),
            )
        )
        return ProcedureHandle(self, receipt.run.procedure_run_id)

    def recovery_availability[SourceIntent: BaseModel, TargetIntent: BaseModel](
        self,
        adapter: ProcedureRecoveryAdapter[SourceIntent, TargetIntent],
        source_id: str,
    ) -> ProcedureRecoveryAvailability:
        """Offer one explicit, side-effect-free project adapter's frozen plan."""
        source = self.snapshot(source_id)
        try:
            self._registry.resolve(source.definition)
            self._registry.resolve(adapter.source.ref)
            self._registry.resolve(adapter.destination.ref)
            attempts: list[ProcedureStepAttempt] = []
            cursor: int | None = None
            while True:
                page = self.steps(source_id, limit=200, before=cursor)
                attempts.extend(page.items)
                if page.next_cursor is None:
                    break
                cursor = page.next_cursor
            recovery = adapter.source_ref(source, attempts)
            retained = self._session.get_run(recovery.retained_run.run_id).snapshot
            plan = adapter.plan(source, attempts, retained)
        except (ValueError, KeyError) as error:
            return ProcedureRecoveryAvailability(adapter.id, None, str(error))
        return ProcedureRecoveryAvailability(adapter.id, plan, None)

    def submit_recovery(
        self, plan: ProcedureRecoveryPlan, *, request_key: str
    ) -> ProcedureHandle:
        """Create a linked run; server rechecks facts atomically before admission."""
        self._registry.resolve(plan.recovery.definition)
        destination = self._registry.resolve(plan.definition)
        receipt = self._client.submit_procedure(
            ProcedureSubmitCommand(
                request_key=request_key,
                definition=destination.ref,
                intent=destination.encode_intent(plan.intent),
                samples=plan.samples,
                recovery=plan.recovery,
            )
        )
        return ProcedureHandle(self, receipt.run.procedure_run_id)

    def start(
        self,
        definition: RegisteredProcedure,
        intent: object,
        *,
        request_key: str,
        worker_id: str | None = None,
        sample: str | SampleSelector | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ProcedureHandle:
        selected = self._registry.resolve(definition.ref)
        run = self._worker().execute(
            selected,
            intent,
            request_key,
            self._worker_id if worker_id is None else worker_id,
            samples=_procedure_sample_selectors(sample, samples),
        )
        return ProcedureHandle(self, run.procedure_run_id)

    def cancel(
        self,
        procedure: str | ProcedureHandle,
        *,
        actor: str,
        reason: str,
    ) -> ProcedureHandle:
        """Request cancellation, retaining completed steps and evidence."""
        procedure_id = (
            procedure.id if isinstance(procedure, ProcedureHandle) else procedure
        )
        run = self._client.get_procedure(procedure_id)
        self._client.cancel_procedure(
            ProcedureCancelCommand(
                procedure_run_id=procedure_id,
                expected_run_revision=run.revision,
                actor=actor,
                reason=reason,
            )
        )
        return ProcedureHandle(self, procedure_id)

    def resume(
        self,
        procedure: str | ProcedureHandle,
        *,
        worker_id: str | None = None,
    ) -> ProcedureHandle:
        procedure_run_id = (
            procedure.id if isinstance(procedure, ProcedureHandle) else procedure
        )
        run = self._worker().resume(
            procedure_run_id,
            worker_id=self._worker_id if worker_id is None else worker_id,
        )
        return ProcedureHandle(self, run.procedure_run_id)

    def retry_attention(
        self,
        procedure: str | ProcedureHandle,
        *,
        worker_id: str | None = None,
    ) -> ProcedureHandle:
        """Retry the exact quarantined step after reconciling its side effect."""

        procedure_run_id = (
            procedure.id if isinstance(procedure, ProcedureHandle) else procedure
        )
        run = self._client.get_procedure(procedure_run_id)
        if run.state != "attention_required":
            raise ValueError(
                "procedure step attention retry requires an attention-required run"
            )
        step = self._attention_step(procedure_run_id)
        receipt = self._client.retry_procedure_step_attention(
            ProcedureStepAttentionRetryCommand(
                procedure_run_id=procedure_run_id,
                expected_run_revision=run.revision,
                step_key=step.step_key,
                attempt=step.attempt,
                expected_step_revision=step.revision,
            )
        )
        resumed = self._worker().resume_snapshot(
            receipt.run,
            worker_id=self._worker_id if worker_id is None else worker_id,
        )
        return ProcedureHandle(self, resumed.procedure_run_id)

    def respond[ValueT](
        self,
        procedure: str | ProcedureHandle,
        step_key: str,
        value: ValueT,
        *,
        schema: AnalysisFactSchema[ValueT],
        actor: str,
        actor_kind: InterpretationActorKind = "human",
        note: str = "",
    ) -> ProcedureHandle:
        """Validate and commit one answer without implicitly resuming hardware work."""

        procedure_run_id = (
            procedure.id if isinstance(procedure, ProcedureHandle) else procedure
        )
        run = self._client.get_procedure(procedure_run_id)
        if run.state != "waiting_for_input":
            raise ValueError("procedure is not waiting for interpretation input")
        step = self._waiting_step(procedure_run_id, step_key=step_key)
        request = step.interpretation_request
        if request is None:
            raise RuntimeError("waiting interpretation has no durable request")
        if (
            request.schema_id != schema.id
            or request.schema_codec != schema.schema_codec
            or request.schema_hash != schema.schema_hash
            or request.structure != schema.structure
        ):
            raise ValueError("local response schema does not match the durable request")
        receipt = self._client.submit_procedure_step_input(
            ProcedureStepInputSubmitCommand(
                procedure_run_id=procedure_run_id,
                expected_run_revision=run.revision,
                step_key=step.step_key,
                attempt=step.attempt,
                expected_step_revision=step.revision,
                request_hash=request.request_hash,
                actor=actor,
                actor_kind=actor_kind,
                value=schema.encode(value),
                note=note,
            )
        )
        return ProcedureHandle(self, receipt.run.procedure_run_id)

    def resume_snapshot(
        self,
        run: ProcedureRun,
        *,
        worker_id: str | None = None,
        should_yield: Callable[[], bool] | None = None,
    ) -> ProcedureHandle:
        """Resume one runnable snapshot returned for this exact registry."""

        resumed = self._worker().resume_snapshot(
            run,
            worker_id=self._worker_id if worker_id is None else worker_id,
            should_yield=should_yield,
        )
        return ProcedureHandle(self, resumed.procedure_run_id)

    def get(self, procedure_run_id: str) -> ProcedureHandle:
        self._client.get_procedure(procedure_run_id)
        return ProcedureHandle(self, procedure_run_id)

    def snapshot(self, procedure_run_id: str) -> ProcedureRun:
        return self._client.get_procedure(procedure_run_id)

    def steps(
        self,
        procedure_run_id: str,
        *,
        limit: int = 50,
        before: int | None = None,
    ) -> ProcedureStepAttemptPage:
        return self._client.list_procedure_step_attempts(
            procedure_run_id,
            ProcedureStepAttemptListQuery(limit=limit, cursor=before),
        )

    def _attention_step(self, procedure_run_id: str) -> ProcedureStepAttempt:
        cursor: int | None = None
        while True:
            page = self.steps(procedure_run_id, limit=200, before=cursor)
            for attempt in page.items:
                if attempt.state == "attention_required":
                    return attempt
            if page.next_cursor is None:
                raise ValueError(
                    "procedure attention is not owned by a retryable step attempt"
                )
            cursor = page.next_cursor

    def _waiting_step(
        self,
        procedure_run_id: str,
        *,
        step_key: str,
    ) -> ProcedureStepAttempt:
        cursor: int | None = None
        while True:
            page = self.steps(procedure_run_id, limit=200, before=cursor)
            for attempt in page.items:
                if (
                    attempt.step_key == step_key
                    and attempt.state == "waiting_for_input"
                ):
                    return attempt
            if page.next_cursor is None:
                raise ValueError(
                    f"procedure has no waiting interpretation step {step_key!r}"
                )
            cursor = page.next_cursor

    def list(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        state: ProcedureRunState | None = None,
    ) -> ProcedureHandlePage:
        page = self._client.list_procedures(
            ProcedureRunListQuery(limit=limit, cursor=before, state=state)
        )
        return ProcedureHandlePage(
            items=tuple(
                ProcedureHandle(self, run.procedure_run_id) for run in page.items
            ),
            next_cursor=page.next_cursor,
        )

    def create_schedule(
        self,
        definition: RegisteredProcedure,
        intent: object,
        *,
        schedule_id: str,
        due_at: datetime,
    ) -> ProcedureSchedule:
        """Create one exact one-shot invocation in the project registry."""

        selected = self._registry.resolve(definition.ref)
        receipt = self._client.create_procedure_schedule(
            ProcedureScheduleCreateCommand(
                schedule_id=schedule_id,
                definition=selected.ref,
                intent=selected.encode_intent(intent),
                due_at=due_at,
            )
        )
        return receipt.schedule

    def get_schedule(self, schedule_id: str) -> ProcedureSchedule:
        return self._client.get_procedure_schedule(schedule_id)

    def list_schedules(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        state: ProcedureScheduleState | None = None,
    ) -> ProcedureSchedulePage:
        return self._client.list_procedure_schedules(
            ProcedureScheduleListQuery(
                limit=limit,
                cursor=before,
                state=state,
            )
        )

    def list_due_schedules(
        self,
        *,
        limit: int = 50,
        cursor: int | None = None,
        through_sequence: int | None = None,
    ) -> ProcedureScheduleDuePage:
        return self._client.list_due_procedure_schedules(
            ProcedureScheduleDueQuery(
                limit=limit,
                cursor=cursor,
                through_sequence=through_sequence,
            )
        )

    def cancel_schedule(
        self,
        schedule_id: str,
        *,
        expected_revision: int,
        actor: str,
        reason: str,
    ) -> ProcedureSchedule:
        receipt = self._client.cancel_procedure_schedule(
            ProcedureScheduleCancelCommand(
                schedule_id=schedule_id,
                expected_schedule_revision=expected_revision,
                actor=actor,
                reason=reason,
            )
        )
        return receipt.schedule

    def materialize_schedule(
        self,
        schedule_id: str,
        *,
        expected_revision: int,
    ) -> ProcedureSchedule:
        receipt = self._client.materialize_procedure_schedule(
            ProcedureScheduleMaterializeCommand(
                schedule_id=schedule_id,
                expected_schedule_revision=expected_revision,
            )
        )
        return receipt.schedule

    def list_runnable(self, *, limit: int = 50) -> ProcedureRunnablePage:
        """List work matching exact definitions loaded by this application."""

        return self._client.list_runnable_procedures(
            ProcedureRunnableQuery(definitions=self._registry.refs, limit=limit)
        )

    def _worker(self) -> ProcedureWorker:
        return ProcedureWorker(
            self._client,
            self._registry,
            context_factory=lambda durable: LabProcedureContext(
                durable,
                runner=self._runner,
                config=self._config,
                session=self._session,
            ),
        )


def _experiment_invocation(experiment: ExperimentSpec) -> ExperimentInvocation:
    return experiment.bind() if isinstance(experiment, Experiment) else experiment


def _procedure_analysis_key(procedure_run_id: str, step_key: str) -> str:
    digest = stable_content_hash(
        {
            "procedure_run_id": procedure_run_id,
            "step_key": step_key,
        }
    )
    label = artifact_slug(step_key, fallback="analysis")[:32]
    return f"procedure-{label}-{digest}"


def _analysis_step_intent_hash(
    *,
    procedure_run_id: str,
    step_key: str,
    analysis: AnalysisStep,
    key: str,
    inputs: tuple[ProcedureStepOutputRef, ...],
) -> Sha256ContentHash:
    identity = {
        "codec": _ANALYSIS_STEP_INTENT_CODEC,
        "procedure_run_id": procedure_run_id,
        "step_key": step_key,
        "analysis_id": analysis.id,
        "analysis_implementation": _analysis_implementation_fingerprint(analysis),
        "analysis_arguments": _analysis_arguments_identity(analysis),
        "analysis_key": key,
        "inputs": [item.model_dump(mode="json") for item in inputs],
    }
    return f"sha256:{stable_content_hash(identity)}"


def _verified_candidate_publish_step_intent_hash(
    *,
    procedure_run_id: str,
    step_key: str,
    candidate_ref: AnalysisPublicationOutputRef,
    proposal_id: str,
    verification: AnalysisPublicationOutputRef,
    decision_output_id: str,
    expected_generation: int,
    actor: str,
    entry_id: str,
    note: str,
) -> Sha256ContentHash:
    identity = {
        "codec": _VERIFIED_CANDIDATE_PUBLISH_STEP_INTENT_CODEC,
        "procedure_run_id": procedure_run_id,
        "step_key": step_key,
        "candidate": candidate_ref.model_dump(mode="json"),
        "proposal_id": proposal_id,
        "verification": verification.model_dump(mode="json"),
        "decision_output_id": decision_output_id,
        "expected_generation": expected_generation,
        "actor": actor,
        "entry_id": entry_id,
        "note": note,
    }
    return f"sha256:{stable_content_hash(identity)}"


def _analysis_arguments_identity(analysis: AnalysisStep) -> object:
    if not isinstance(analysis, AnalysisInvocation):
        return {
            "kind": "analysis_step",
            "type": f"{type(analysis).__module__}.{type(analysis).__qualname__}",
        }
    return {
        "kind": "analysis_invocation",
        "arguments": [
            [name, _analysis_argument_identity(value)]
            for name, value in analysis.arguments
        ],
    }


def _analysis_implementation_fingerprint(
    analysis: AnalysisStep,
) -> Sha256ContentHash:
    if isinstance(analysis, AnalysisInvocation):
        return analysis.implementation_fingerprint
    implementation = type(analysis)
    try:
        state = content_fingerprint(analysis)
    except TypeError as error:
        try:
            state = content_fingerprint(vars(analysis))
        except TypeError:
            raise TypeError(
                "custom procedure analysis steps with opaque state must implement "
                "__scopecat_fingerprint__()"
            ) from error
    identity = {
        "codec": "scopecat.analysis-implementation.v1",
        "id": analysis.id,
        **python_source_identity(
            implementation,
            label="custom procedure analysis",
        ),
        "state": state,
    }
    return f"sha256:{stable_content_hash(identity)}"


def _analysis_argument_identity(value: object) -> object:
    if isinstance(value, RunHandle):
        return {"kind": "run", "run_id": value.id}
    if isinstance(value, PublishedAnalysis):
        return {
            "kind": "analysis",
            "subject": value.view.analysis.subject.model_dump(mode="json"),
            "analysis_record_id": value.id,
        }
    return content_fingerprint(value)


def _existing_run_analysis(
    run: RunHandle,
    *,
    key: str,
) -> PublishedAnalysis | None:
    try:
        return run.published_analysis(analysis_record_id(key, 1))
    except DaemonNotFoundError:
        return None


def _existing_project_analysis(
    lab: ProcedureLabSession,
    *,
    key: str,
) -> PublishedAnalysis | None:
    try:
        return lab.published_analysis(analysis_record_id(key, 1))
    except DaemonNotFoundError:
        return None


def _validate_run_analysis(
    published: PublishedAnalysis,
    *,
    run_id: str,
    step_id: str,
    key: str,
    inputs: tuple[ProcedureStepOutputRef, ...],
) -> None:
    subject = published.view.analysis.subject
    if not isinstance(subject, RunAnalysisSubject) or subject.run_id != run_id:
        raise ValueError("procedure run analysis has the wrong exact subject")
    if published.step_id != step_id or published.key != key:
        raise ValueError("procedure run analysis identity does not match its step")
    if published.id != analysis_record_id(key, 1) or published.revision != 1:
        raise ValueError("procedure run analysis must resolve the exact first revision")
    _validate_analysis_upstreams(published, inputs=inputs)


def _validate_project_analysis(
    published: PublishedAnalysis,
    *,
    step_id: str,
    key: str,
    inputs: tuple[ProcedureStepOutputRef, ...],
) -> None:
    if not isinstance(published.view.analysis.subject, ProjectAnalysisSubject):
        raise ValueError("procedure project analysis has the wrong subject")
    if published.step_id != step_id or published.key != key:
        raise ValueError("procedure project analysis identity does not match its step")
    if published.id != analysis_record_id(key, 1) or published.revision != 1:
        raise ValueError(
            "procedure project analysis must resolve the exact first revision"
        )
    _validate_analysis_upstreams(published, inputs=inputs)


def _validate_analysis_upstreams(
    published: PublishedAnalysis,
    *,
    inputs: tuple[ProcedureStepOutputRef, ...],
) -> None:
    """Compare durable owner-level refs, not individual publication outputs."""

    declared_inputs = {
        (
            item.analysis_reference
            if isinstance(item, InterpretationOutputRef)
            else item
        ).model_dump_json()
        for item in inputs
    }
    actual_inputs: set[str] = set()
    for item in published.inputs:
        if isinstance(item, MeasurementAnalysisRecordInput):
            actual_inputs.add(RunOutputRef(run_id=item.run_id).model_dump_json())
        elif isinstance(item, InterpretationAnalysisRecordInput):
            actual_inputs.add(item.source.model_dump_json())
        else:
            actual_inputs.add(
                AnalysisPublicationOutputRef(
                    subject=item.source.subject,
                    analysis_record_id=item.source.analysis_record_id,
                ).model_dump_json()
            )
    if actual_inputs != declared_inputs:
        raise ValueError(
            "procedure analysis upstreams do not match declared durable inputs"
        )


def _validate_exact_analysis_ref(
    published: PublishedAnalysis,
    ref: AnalysisPublicationOutputRef,
) -> None:
    if (
        published.id != ref.analysis_record_id
        or published.view.analysis.subject != ref.subject
    ):
        raise ValueError(
            "published analysis does not match its exact procedure reference"
        )


def _parameter_proposal(
    published: PublishedAnalysis,
    proposal_id: str,
) -> ParameterChangeProposal:
    try:
        return next(
            item for item in published.parameter_proposals if item.id == proposal_id
        )
    except StopIteration:
        raise ValueError(
            f"candidate analysis does not own proposal {proposal_id!r}"
        ) from None


def _execute_replayable_config_operation[ReceiptT](
    *,
    operation_id: str,
    operation_name: str,
    execute: Callable[[], ReceiptT],
    lookup: Callable[[str], ReceiptT],
    validate: Callable[[ReceiptT], None],
) -> ReceiptT:
    try:
        receipt = execute()
        validate(receipt)
        return receipt
    except (
        httpx2.HTTPError,
        DaemonUnavailableError,
        ProcedureNeedsAttention,
        ValidationError,
    ):
        try:
            receipt = lookup(operation_id)
            validate(receipt)
            return receipt
        except (
            DaemonClientError,
            httpx2.HTTPError,
            ProcedureNeedsAttention,
            ValidationError,
        ) as lookup_error:
            raise ProcedureNeedsAttention(
                f"{operation_name} outcome for operation {operation_id!r} is unknown"
            ) from lookup_error


def _validate_config_activation_receipt(
    receipt: ConfigActivationReceipt,
    *,
    operation_id: str,
    intent_hash: Sha256ContentHash,
    entry_id: str,
    expected_generation: int,
    actor: str,
    note: str,
) -> None:
    operation = receipt.operation
    expected_identity = (
        operation_id,
        intent_hash,
        entry_id,
        expected_generation,
        actor,
        note,
    )
    actual_identity = (
        operation.operation_id,
        operation.intent_hash,
        operation.entry_id,
        operation.expected_generation,
        operation.actor,
        operation.note,
    )
    if actual_identity != expected_identity:
        raise ProcedureNeedsAttention(
            "config activation receipt does not match its exact procedure command"
        )


def _validate_config_publish_receipt(
    receipt: ConfigPublishReceipt,
    *,
    command: ConfigPublishCommand,
    proposal_base_config_content_hash: Sha256ContentHash,
) -> None:
    source = command.source
    if not isinstance(source, CandidateConfigRevisionSource):
        raise TypeError("procedure candidate publication requires a candidate source")
    operation = receipt.operation
    expected_operation_identity = (
        command.operation_id,
        command.intent_hash,
        command.source_intent_hash,
        command.entry_id,
        command.expected_generation,
        command.actor,
        command.note,
    )
    actual_operation_identity = (
        operation.operation_id,
        operation.intent_hash,
        operation.source_intent_hash,
        operation.entry_id,
        operation.expected_generation,
        operation.actor,
        operation.note,
    )
    entry_source = receipt.entry.source
    if (
        actual_operation_identity != expected_operation_identity
        or operation.activation_generation != receipt.activation.generation
        or receipt.entry.id != command.entry_id
        or receipt.activation.entry_id != receipt.entry.id
        or receipt.activation.entry_content_hash != receipt.entry.content_hash
        or not isinstance(entry_source, CandidateConfigRegistrySource)
        or entry_source.run_id != source.run_id
        or entry_source.proposal_id != source.proposal_id
        or entry_source.base_config_content_hash != proposal_base_config_content_hash
        or entry_source.acceptance != source.acceptance
    ):
        raise ProcedureNeedsAttention(
            "config publish receipt does not match its exact procedure command"
        )


def _procedure_sample_selectors(
    sample: str | SampleSelector | None,
    samples: tuple[SampleSelector, ...],
) -> tuple[SampleSelector, ...]:
    if sample is None:
        return samples
    if samples:
        raise ValueError("sample and samples cannot be combined")
    if isinstance(sample, SampleSelector):
        return (sample,)
    return (SampleSelector(sample_id=sample),)


__all__ = [
    "InterpretationResult",
    "LabProcedureContext",
    "LabProcedureOperations",
    "ProcedureHandle",
    "ProcedureHandlePage",
    "ProcedureLabSession",
    "ProcedureSummary",
]
