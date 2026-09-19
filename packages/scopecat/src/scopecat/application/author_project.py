"""Notebook entry point with project imports isolated by source revision."""

from __future__ import annotations

import os
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, SupportsFloat, Unpack, overload, override
from uuid import uuid4

import httpx2
from pydantic import JsonValue, TypeAdapter

from scopecat.analysis.arguments import AnalysisArgument, encode_arguments
from scopecat.analysis.facts import ordinary_result_schema
from scopecat.api._config import LabConfigOperations
from scopecat.api._remote import RemoteRunOperations
from scopecat.api.parameter_candidates import ParameterCandidate
from scopecat.api.parameters import ParameterWorkspace
from scopecat.api.published_analysis import (
    AnalysisGroupResult,
    AnalysisResult,
    GroupedAnalysisResult,
)
from scopecat.api.run import RunHandle
from scopecat.application.authoring import AuthorExperiment
from scopecat.application.experiment_plans import plan_definition, plan_launch_request
from scopecat.application.inspection import LaunchInspection
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchField,
    LaunchPreview,
    LaunchSubmission,
)
from scopecat.application.session_context import (
    INHERIT,
    SessionContext,
    SessionContextUpdate,
    SessionDefault,
)
from scopecat.authoring.experiments import Experiment, ExperimentRequest, Scan
from scopecat.automation.models import ProcedureRun, RunOutputRef
from scopecat.automation.wire import (
    ProcedureCancelCommand,
    ProcedureRunListQuery,
    ProcedureStepAttemptListQuery,
)
from scopecat.config.candidates import CandidateConfig
from scopecat.daemon.client import (
    DaemonClient,
    DaemonNotFoundError,
    DaemonUnavailableError,
)
from scopecat.daemon.preparation import AuthorPreparationOperation
from scopecat.daemon.procedure_views import ProcedureOperatorView
from scopecat.daemon.views import MeasurementLivePreview, MeasurementPreview
from scopecat.kernel.errors import SessionClosedError
from scopecat.kernel.quantity import Quantity
from scopecat.project_sources import SourceProject, capture_sources
from scopecat.records.analysis_grouping import AnalysisGrouping
from scopecat.records.author_revision import (
    AuthorAnalysisGroupReceipt,
    AuthorAnalysisReceipt,
    AuthorAnalysisRequest,
    AuthorPreparationRequest,
    AuthorRevisionRef,
    AuthorRevisionState,
)
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.control_edit import ControlEdit
from scopecat.records.experiment_plan import ExperimentPlanRevision, ExperimentPlanSave
from scopecat.records.experimental_batch import require_batch_match
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.measurement import MeasurementRecord
from scopecat.records.parameter_update import ParameterUpdate
from scopecat.records.plan_ref import ExperimentPlanRef, PlanAnalysisSource
from scopecat.records.run import AnalysisCandidateRunConfigSource
from scopecat.records.run_request import AxisValuesSourceRecord

if TYPE_CHECKING:
    from scopecat.application.live_experiment import LiveExperiment
    from scopecat.application.run_history import RunHistory


class AuthorProject(DaemonClient):
    """Refresh author code and explicitly rebind typed notebook declarations.

    Prepared requests retain their source revision. Typed analysis uses the run's
    original source by default; explicitly refresh and select current to reanalyze.
    """

    def __init__(
        self,
        base_url: str,
        *,
        receipts: Path | None = None,
        project_root: Path | None = None,
        source_project: SourceProject | None = None,
        timeout: float | httpx2.Timeout | None = 120,
        transport: httpx2.BaseTransport | None = None,
    ) -> None:
        from scopecat.author_workspaces import author_workspace_id

        super().__init__(
            base_url,
            timeout=timeout,
            transport=transport,
            workspace_id=author_workspace_id(project_root)
            if project_root is not None
            else "legacy",
        )
        self.receipts = receipts.resolve() if receipts is not None else None
        self.project_root = project_root.resolve() if project_root is not None else None
        self._source_project = source_project
        self._selection = SessionContext()

    @property
    def selection(self) -> SessionContext:
        """Read this client's current defaults without refreshing code or state."""
        return self._selection

    def use(self, **changes: Unpack[SessionContextUpdate]) -> SessionContext:
        """Validate and atomically update this client's defaults for future work.

        Omitted fields stay selected; None clears an optional selection. An explicit
        working point selects its sample unless a sample is supplied alongside it.
        Selection never activates configuration or submits hardware operations.
        """
        if self.is_closed:
            raise SessionClosedError("Cannot select context on a closed session")
        selected = SessionContext.model_validate(
            {**self._selection.model_dump(), **changes}
        )
        if selected.working_point is not None:
            source = self.config.resolve_context(selected.working_point).config_source
            if "working_point" in changes and "batch" not in changes:
                selected = selected.model_copy(update={"batch": source.sample.batch_id})
            require_batch_match(selected.batch, source.sample.batch_id)
            selected = selected.model_copy(update={"batch": source.sample.batch_id})
            if "working_point" in changes and "sample" not in changes:
                selected = selected.model_copy(
                    update={"sample": source.sample.sample_id}
                )
            elif (
                selected.sample is not None
                and selected.sample != source.sample.sample_id
            ):
                raise ValueError(
                    "sample does not match the selected working point; "
                    "select its working point or clear working_point=None"
                )
            else:
                selected = selected.model_copy(
                    update={"sample": source.sample.sample_id}
                )
        elif selected.sample is not None:
            self.get_sample(selected.sample)
        if selected.batch is not None:
            self.experimental_batch(selected.batch)
            if selected.sample is None:
                raise ValueError("batch selection requires a sample or working point")
        if selected.collection is not None:
            self.record_collection(selected.collection)
        self._selection = selected
        return selected

    @property
    def run_operations(self) -> RemoteRunOperations:
        return RemoteRunOperations(self)

    @property
    def config(self) -> LabConfigOperations:
        return LabConfigOperations(
            self, self.run_operations, None, self._selection.operator
        )

    def live[**P, ResultT](
        self, experiment: Experiment[P, ResultT]
    ) -> LiveExperiment[P, ResultT]:
        """Use saved source for each new request; existing requests stay frozen."""
        from scopecat.application.live_experiment import LiveExperiment

        self._require_local_authoring()
        if self._source_project is None:
            raise ValueError("Live experiments require project.authoring().")
        return LiveExperiment(self._live_revision, lambda: self.refresh(experiment))

    def _live_revision(self) -> AuthorRevisionRef:
        if self.is_closed:
            raise SessionClosedError(
                "Author session is closed; create a new live experiment."
            )
        assert self._source_project is not None
        return capture_sources(self._source_project).manifest.ref

    def history(
        self,
        *,
        limit: int = 20,
        before: int | None = None,
        collection: str | SessionDefault | None = INHERIT,
    ) -> RunHistory:
        """Display a bounded page using collection or legacy store-local numbers."""
        from scopecat.application.run_history import RunHistory
        from scopecat.records.research_project import RunHistoryFilter

        if isinstance(collection, SessionDefault):
            collection = self._selection.collection
        return RunHistory(
            self.list_runs(
                limit=limit,
                before=before,
                history=RunHistoryFilter(record_collection=collection),
            ),
            collection=collection,
        )

    def run_number(
        self, run: RunHandle | str, *, collection: str | SessionDefault | None = INHERIT
    ) -> int:
        """Return a number in the selected collection, or the legacy store scope."""
        if isinstance(run, RunHandle) and run.session is not self:
            raise ValueError("Use a run from this session or an explicit run id")
        if isinstance(collection, SessionDefault):
            collection = self._selection.collection
        detail = self.get_run(run.id if isinstance(run, RunHandle) else run)
        if collection is None:
            return detail.control.sequence
        if detail.address is None or detail.address.collection_id != collection:
            raise ValueError("run belongs to another record collection")
        return detail.address.number

    def run(
        self, run_id: str | int, *, collection: str | SessionDefault | None = INHERIT
    ) -> RunHandle:
        """Reconnect by durable id or by number in this client's selected scope."""
        if not isinstance(run_id, int):
            if collection is not None and not isinstance(collection, SessionDefault):
                raise ValueError(
                    "collection qualifies an integer run number; use a run id alone"
                )
            return RunHandle(self, run_id)
        if isinstance(run_id, bool) or run_id < 1:
            raise ValueError("run number must be a positive integer")
        if isinstance(collection, SessionDefault):
            collection = self._selection.collection
        if collection is not None:
            return RunHandle(self, self.resolve_run_number(collection, run_id).run_id)
        page = self.list_runs(limit=1, before=run_id + 1)
        if not page.items or page.items[0].control.sequence != run_id:
            raise KeyError(f"No run #{run_id} in this project")
        return RunHandle(self, page.items[0].run_id)

    def reopen(self, receipt: str | Path) -> AuthorJob:
        """Read a saved job receipt; recovery is read-only and never resubmits."""
        path = Path(receipt).resolve()
        return AuthorJob(
            self,
            path,
            LaunchRequest.model_validate_json(path.read_text(encoding="utf-8")),
        )

    @override
    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        json: object | None = None,
        content: bytes | Iterable[bytes] | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> httpx2.Response:
        if self.is_closed:
            raise SessionClosedError(
                "Author session is closed. Open project.authoring() again, then "
                "job.reconnect(session) or session.run(run_id) for retained data."
            )
        return super()._request(
            method,
            path,
            params=params,
            json=json,
            content=content,
            headers=headers,
            timeout=timeout,
        )

    def _default_request_revision(self) -> AuthorRevisionRef | None:
        """Interactive sessions may select a source before creating a new preview."""
        return None

    def prepare(
        self,
        experiment: str | ExperimentRequest[object, object],
        *,
        control_edits: dict[str, ControlEdit] | None = None,
        fixed: Mapping[str, SupportsFloat | Quantity] | None = None,
        scans: Mapping[str, Iterable[SupportsFloat | Quantity]] | None = None,
        parameters: ParameterWorkspace | None = None,
        candidate: ParameterCandidate | CandidateConfig | None = None,
        code_revision: AuthorRevisionRef | None = None,
        inputs: dict[str, JsonValue] | None = None,
        context: ConfigContextRef | SessionDefault | None = INHERIT,
        overrides: tuple[ParameterUpdate, ...] = (),
        sample: str | SessionDefault | None = INHERIT,
        actor: str | SessionDefault = INHERIT,
        batch: str | SessionDefault | None = INHERIT,
        record_collection: str | SessionDefault | None = INHERIT,
    ) -> AuthorPreparedLaunch:
        """Select the current declaration and retain a preview's exact submission."""
        selection = self._selection
        batch = selection.batch if isinstance(batch, SessionDefault) else batch
        context, sample = selection.scientific_scope(
            context=context,
            sample=sample,
            explicit_parameters=parameters is not None or candidate is not None,
        )
        actor = selection.operator if isinstance(actor, SessionDefault) else actor
        record_collection = (
            selection.collection
            if isinstance(record_collection, SessionDefault)
            else record_collection
        )
        draft = experiment.copy() if isinstance(experiment, ExperimentRequest) else None
        if draft is not None:
            retained_revision = draft.declaration.code_revision
            if (
                retained_revision is not None
                and code_revision is not None
                and code_revision != retained_revision
            ):
                raise ValueError(
                    "request declaration belongs to another source revision"
                )
            code_revision = retained_revision or code_revision
            if any(
                value is not None for value in (inputs, control_edits, fixed, scans)
            ):
                raise ValueError(
                    "request.values already selects inputs and scans; edit the request"
                )
            experiment = draft.declaration.id
        candidate_source = None
        sample_binding = None
        if candidate is not None:
            if (
                parameters is not None
                or context is not None
                or overrides
                or sample is not None
            ):
                raise ValueError(
                    "candidate selects its exact parameters, sample and workpoint"
                )
            selected = (
                candidate.config
                if isinstance(candidate, ParameterCandidate)
                else candidate
            )
            _, candidate_source = self.config.resolve_with_source(selected)
            assert isinstance(candidate_source, AnalysisCandidateRunConfigSource)
            subjects = [
                item
                for item in self.run(selected.source_run_id).samples
                if item.role == "subject"
            ]
            if len(subjects) != 1:
                raise ValueError(
                    "candidate requires one exact subject sample/workpoint"
                )
            sample_binding = subjects[0]
            sample = sample_binding.sample_id
        catalog = self.catalog(
            code_revision=code_revision or self._default_request_revision()
        )
        edits = dict(control_edits or {})
        for name, value in (fixed or {}).items():
            if name in edits:
                raise ValueError(f"{name}: choose one fixed or scanned input")
            edits[name] = ControlEdit(mode="fixed", value=_control_value(value))
        for name, values in (scans or {}).items():
            if name in edits:
                raise ValueError(f"{name}: choose one fixed or scanned input")
            edits[name] = ControlEdit(
                mode="scan",
                axis=AxisValuesSourceRecord(
                    values=[_control_value(value) for value in values]
                ),
            )
        if parameters is not None:
            if context is not None or overrides or sample is not None:
                raise ValueError("parameters already selects the context and sample")
            frozen = parameters.freeze()
            context = frozen.config_source.context
            overrides = frozen.config_source.overrides
            sample = frozen.config_source.sample.sample_id
        entry = next((item for item in catalog.entries if item.id == experiment), None)
        if entry is None:
            raise ValueError(
                f"Unknown experiment {experiment!r}; "
                "inspect author.catalog() for available names"
            )
        if draft is not None:
            declaration = AuthorExperiment.from_declaration(
                draft.declaration, code_revision=catalog.code_revision
            )
            if declaration.fingerprint != entry.version:
                raise ValueError(
                    f"{entry.id}: imported declaration does not match "
                    "the selected source revision; "
                    "reload the experiment module or explicitly select "
                    "its original revision"
                )
            values = draft.snapshot()
            for control in declaration.controls.fields:
                if control.id not in values:
                    continue
                value = values.pop(control.id)
                if isinstance(value, Scan):
                    if not control.scannable:
                        raise ValueError(f"{control.id}: input is not scannable")
                    edits[control.id] = ControlEdit(
                        mode="scan",
                        axis=AxisValuesSourceRecord(
                            values=[control.normalize(item) for item in value.values]
                        ),
                    )
                else:
                    edits[control.id] = ControlEdit(
                        mode="fixed", value=control.normalize(value)
                    )
            inputs = declaration.input_model.model_validate(values).model_dump(
                mode="json"
            )
        declared_inputs = {
            name: field.default
            for name, field in entry.request.properties.items()
            if isinstance(field, LaunchField) and "default" in field.model_fields_set
        }
        request = LaunchRequest(
            action="preview",
            experiment=entry.id,
            version=entry.version,
            control_edits=edits,
            scan_mode=draft.scan_mode if draft else "cartesian",
            parameter_sweeps=draft.parameter_sweeps if draft else (),
            inputs=declared_inputs | (inputs or {}),
            config_source=candidate_source,
            sample_binding=sample_binding,
            context=context,
            overrides=overrides,
            sample=sample,
            actor=actor,
            batch_id=batch,
            record_collection=record_collection,
            code_revision=catalog.code_revision,
            workspace_id=catalog.workspace_id,
        )
        return AuthorPreparedLaunch(self, request, self.preview(request))

    def prepare_plan(
        self,
        ref: ExperimentPlanRef,
        *,
        actor: str | SessionDefault = INHERIT,
        batch: str | SessionDefault | None = INHERIT,
        record_collection: str | SessionDefault | None = INHERIT,
    ) -> AuthorPreparedLaunch:
        """Read an exact plan and obtain a new preview for this execution actor."""
        actor = self._selection.operator if isinstance(actor, SessionDefault) else actor
        record_collection = (
            self._selection.collection
            if isinstance(record_collection, SessionDefault)
            else record_collection
        )
        request = plan_launch_request(
            self.experiment_plan(ref), actor=actor, record_collection=record_collection
        )
        selected_batch = (
            self._selection.batch if isinstance(batch, SessionDefault) else batch
        )
        require_batch_match(selected_batch, request.batch_id)
        return AuthorPreparedLaunch(self, request, self.preview(request))

    def state(self) -> AuthorRevisionState:
        return self.author_revision_state()

    def preparation(self, operation_id: str) -> AuthorPreparationOperation:
        """Reconnect by identity without capturing or publishing new source."""
        return AuthorPreparationOperation(self, operation_id)

    def begin_refresh(
        self, *, expected_generation: int | None = None, operation_id: str | None = None
    ) -> AuthorPreparationOperation:
        """Capture once; reuse operation_id after uncertain submission."""
        from uuid import uuid4

        if operation_id is not None:
            try:
                operation = self.author_preparation(operation_id)
            except DaemonNotFoundError:
                pass
            else:
                if (
                    expected_generation is not None
                    and operation.expected_generation != expected_generation
                ):
                    raise ValueError(
                        "operation identity already has another generation"
                    )
                return self.preparation(operation_id)
        state = self._get_model("/api/v1/author-revisions", AuthorRevisionState)
        identity = operation_id or uuid4().hex
        self.start_author_preparation(
            AuthorPreparationRequest(
                operation_id=identity,
                expected_generation=state.generation
                if expected_generation is None
                else expected_generation,
            )
        )
        return self.preparation(identity)

    @overload
    def refresh[**P, ResultT](
        self,
        experiment: Experiment[P, ResultT],
        *,
        expected_generation: int | None = None,
        timeout: float | None = None,
    ) -> Experiment[P, ResultT]: ...

    @overload
    def refresh(
        self,
        experiment: None = None,
        *,
        expected_generation: int | None = None,
        timeout: float | None = None,
    ) -> AuthorRevisionState: ...

    def refresh[**P, ResultT](
        self,
        experiment: Experiment[P, ResultT] | None = None,
        *,
        expected_generation: int | None = None,
        timeout: float | None = None,
    ) -> AuthorRevisionState | Experiment[P, ResultT]:
        """Wait for explicit source refresh; wait expiry never cancels publication.

        Existing prepared requests keep their original code. The timeout exception
        carries its operation handle; it is safe to wait again or reconnect.
        """
        if experiment is not None:
            self._require_local_authoring()
        state = self.begin_refresh(expected_generation=expected_generation).wait(
            timeout=timeout
        )
        if experiment is None:
            if (
                state.active is not None
                and self.project_root is not None
                and self.receipts is not None
            ):
                from scopecat.application.author_imports import refresh_revision_imports

                root, cache = self._require_local_authoring()
                catalog = self.catalog(code_revision=state.active)
                try:
                    refresh_revision_imports(
                        self.author_revision(state.active),
                        project_root=root,
                        cache=cache,
                        fingerprints={
                            entry.id: entry.version for entry in catalog.entries
                        },
                    )
                except Exception as error:
                    error.add_note(
                        f"Notebook imports failed for {state.active.content_hash}. "
                        "Server publication is unchanged; fix the local environment "
                        "and refresh again."
                    )
                    raise
            return state
        return self.load_experiment(experiment, code_revision=state.active)

    def _require_local_authoring(self) -> tuple[Path, Path]:
        if self.project_root is None or self.receipts is None:
            raise ValueError("typed refresh requires open_project(...).authoring()")
        return self.project_root, self.receipts.parent / "author-sources"

    def load_experiment[**P, ResultT](
        self,
        experiment: Experiment[P, ResultT],
        *,
        code_revision: AuthorRevisionRef | None = None,
    ) -> Experiment[P, ResultT]:
        """Bind a declaration to admitted source without publishing another revision.

        Use after reconnecting to a timed-out refresh operation. Reassign the
        returned declaration explicitly; existing notebook aliases stay unchanged.
        """
        from scopecat.application.author_imports import load_revision_experiment

        root, cache = self._require_local_authoring()
        catalog = self.catalog(code_revision=code_revision)
        if catalog.code_revision is None:
            raise ValueError("project has no admitted author revision")
        entry = next(
            (item for item in catalog.entries if item.id == experiment.id), None
        )
        if entry is None:
            raise ValueError(
                f"{experiment.id} is absent from the selected author catalog"
            )
        try:
            return load_revision_experiment(
                experiment,
                self.author_revision(catalog.code_revision),
                project_root=root,
                cache=cache,
                expected_fingerprint=entry.version,
                fingerprints={item.id: item.version for item in catalog.entries},
            )
        except Exception as error:
            error.add_note(
                f"Notebook binding failed for {catalog.code_revision.content_hash}. "
                "Server publication is unchanged; fix the local import environment "
                "and retry load_experiment with this code_revision."
            )
            raise

    def catalog(
        self, *, code_revision: AuthorRevisionRef | None = None
    ) -> LaunchCatalog:
        if code_revision is None:
            code_revision = self.state().active
        return self._get_model(
            "/api/v1/experiment-launcher",
            LaunchCatalog,
            params={"code_revision": code_revision.content_hash}
            if code_revision
            else {},
        )

    def preview(self, request: LaunchRequest) -> LaunchPreview:
        return self._post_model(
            "/api/v1/experiment-launcher/preview", request, LaunchPreview
        )

    def submit(self, request: LaunchRequest) -> LaunchSubmission:
        return self._post_model(
            "/api/v1/experiment-launcher/submit", request, LaunchSubmission
        )

    def analyze(
        self,
        run_id: str,
        analysis: str,
        *,
        code_revision: AuthorRevisionRef,
        workspace_id: str | None = None,
        key: str | None = None,
        arguments: Mapping[str, AnalysisArgument] | None = None,
        grouping: AnalysisGrouping | None = None,
    ) -> AuthorAnalysisReceipt:
        return self.analyze_author_revision(
            AuthorAnalysisRequest(
                run_id=run_id,
                analysis=analysis,
                code_revision=code_revision,
                workspace_id=workspace_id or self.workspace_id,
                key=key,
                grouping=grouping,
                arguments=encode_arguments(analysis, arguments),
            )
        )

    def analyze_as[ResultT](
        self,
        run_id: str,
        analysis: str,
        result_type: type[ResultT],
        *,
        source: Literal["original", "current"] = "original",
        arguments: Mapping[str, AnalysisArgument] | None = None,
        key: str | None = None,
    ) -> AnalysisResult[ResultT]:
        """Publish registered analysis and reconstruct a materialized conclusion.

        Original source comes from the retained run. For edited source, call
        refresh() explicitly, then select source="current". Changed arguments
        or source create a separate revision; identical publication may reuse
        its existing receipt. Direct notebook function imports are never sent.
        """
        schema = ordinary_result_schema(result_type)
        run = self.run(run_id)
        revision = self._analysis_revision(run_id, source)
        try:
            receipt = self.analyze(
                run_id,
                analysis,
                code_revision=revision,
                workspace_id=self._analysis_workspace(run_id, source),
                arguments=arguments,
                key=key,
            )
        except httpx2.HTTPStatusError as error:
            error.add_note(error.response.text)
            raise
        publication = run.published_analysis(receipt.analysis_id)
        return AnalysisResult(publication.fact_as("result", schema), publication)

    def _analysis_workspace(
        self, run_id: str, source: Literal["original", "current"]
    ) -> str:
        if source == "current":
            return self.workspace_id
        value = self.run(run_id).request.metadata.get("author_workspace")
        if not isinstance(value, str):
            raise ValueError("Run has no recorded author workspace")
        return value

    def _analysis_revision(
        self, run_id: str, source: Literal["original", "current"]
    ) -> AuthorRevisionRef:
        run = self.run(run_id)
        if source == "original":
            revision_hash = run.request.metadata.get("author_code_revision")
            if not isinstance(revision_hash, str):
                raise ValueError(
                    "Run has no retained author source; "
                    "explicitly select source='current'"
                )
            revision = AuthorRevisionRef(content_hash=revision_hash)
        elif source == "current":
            revision = self.state().active
            if revision is None:
                raise ValueError("Refresh author source before analysis")
        else:
            raise ValueError("analysis source must be original or current")
        return revision

    def analyze_groups_as[ResultT](
        self,
        run_id: str,
        analysis: str,
        result_type: type[ResultT],
        *,
        by: tuple[str, ...],
        fitting: str,
        repeats: Literal["separate", "combine"] = "separate",
        source: Literal["original", "current"] = "original",
        arguments: Mapping[str, AnalysisArgument] | None = None,
        key: str | None = None,
    ) -> GroupedAnalysisResult[ResultT]:
        """Apply one ordinary function to explicit complete offline groups.

        Each success or failure and its exact point selection is retained. No
        averaging is implicit; combine passes repeats to the scientific function.
        """
        ordinary_result_schema(result_type)
        try:
            receipt = self.analyze(
                run_id,
                analysis,
                code_revision=self._analysis_revision(run_id, source),
                workspace_id=self._analysis_workspace(run_id, source),
                arguments=arguments,
                key=key,
                grouping=AnalysisGrouping(by=by, fitting=fitting, repeats=repeats),
            )
        except httpx2.HTTPStatusError as error:
            error.add_note(error.response.text)
            raise
        return self.read_groups_as(run_id, receipt.analysis_id, result_type)

    def read_groups_as[ResultT](
        self,
        run_id: str,
        publication_id: str,
        result_type: type[ResultT],
    ) -> GroupedAnalysisResult[ResultT]:
        """Read an existing group manifest and native results without executing code."""
        schema = ordinary_result_schema(result_type)
        run = self.run(run_id)
        publication = run.published_analysis(publication_id)
        receipts = TypeAdapter(tuple[AuthorAnalysisGroupReceipt, ...]).validate_json(
            publication.artifact("groups").text()
        )
        groups: list[AnalysisGroupResult[ResultT]] = []
        for receipt in receipts:
            child = run.published_analysis(receipt.analysis_id)
            groups.append(
                AnalysisGroupResult(
                    receipt,
                    child.fact_as("result", schema) if receipt.error is None else None,
                    child,
                )
            )
        return GroupedAnalysisResult(tuple(groups), publication)


@dataclass(frozen=True, slots=True)
class AuthorPreparedLaunch:
    client: AuthorProject
    request: LaunchRequest
    preview: LaunchPreview

    @property
    def inspection(self) -> LaunchInspection:
        """Read an isolated copy of captured facts without I/O or recompilation.

        The containing preview retains the exact source/config/request identity.
        Maintained providers must opt in to supplying inspection facts.
        """
        if self.preview.inspection is None:
            raise ValueError(
                "This experiment provider does not expose inspection facts"
            )
        return self.preview.inspection.model_copy(deep=True)

    def save_plan(
        self,
        name: str,
        *,
        saved_by: str,
        previous: ExperimentPlanRef | None = None,
        copied_from: ExperimentPlanRef | None = None,
        source: PlanAnalysisSource | None = None,
    ) -> ExperimentPlanRevision:
        """Save immutable inputs/configuration; no run is admitted or activated."""
        return self.client.save_experiment_plan(
            ExperimentPlanSave(
                name=name,
                saved_by=saved_by,
                previous=previous,
                copied_from=copied_from,
                definition=plan_definition(self.request, self.preview, source=source),
            )
        )

    def run(self) -> AuthorJob:
        """Persist a receipt, then submit this exact preview once.

        An uncertain response exposes its job for read-only recovery. Re-running
        this method intentionally creates a new acquisition; retain the job.
        """
        if self.client.receipts is None:
            raise ValueError(
                "Use project.authoring() or configure a receipts directory"
            )
        key = str(uuid4())
        request = self._submission_request(key)
        job = AuthorJob.retain(
            self.client, self.client.receipts / f"{key}.json", request
        )
        try:
            submitted = self.client.submit(request)
        except httpx2.HTTPStatusError as error:
            if error.response.status_code < 500:
                raise
            raise AuthorSubmissionUncertain(job) from error
        except (httpx2.TransportError, DaemonUnavailableError) as error:
            raise AuthorSubmissionUncertain(job) from error
        if submitted.dispatch_error is not None:
            raise AuthorJobAttention(submitted.dispatch_error, job=job)
        return job

    def _submission_request(self, request_key: str) -> LaunchRequest:
        return LaunchRequest.model_validate(
            {
                **self.request.model_dump(),
                "action": "submit",
                "request_key": request_key,
                "expected_request_hash": self.preview.request_hash,
                "config_source": self.preview.config_source,
                "code_revision": self.preview.code_revision,
                "manual_state": self.preview.manual_state,
            }
        )

    def submit(self, *, request_key: str) -> LaunchSubmission:
        """Low-level submission retaining the explicitly selected request identity."""
        return self.client.submit(self._submission_request(request_key))


class AuthorJobTimeout(TimeoutError):
    """The wait ended; execution has not been cancelled."""


class AuthorJobAttention(RuntimeError):
    """The procedure needs a person's decision; no retry was attempted."""

    def __init__(self, reason: str, *, job: AuthorJob) -> None:
        self.job = job
        super().__init__(reason)


class AuthorJobCancelled(RuntimeError):
    """The procedure has durably closed as cancelled."""


class AuthorJobFailed(RuntimeError):
    """The procedure has durably closed as failed."""


class AuthorSubmissionUncertain(RuntimeError):
    """A submission response was lost. Recover this receipt without resubmitting."""

    def __init__(self, job: AuthorJob) -> None:
        self.job = job
        super().__init__(
            f"Submission outcome is unknown. Reopen {job.receipt} and call recover(); "
            "do not start another run."
        )


@dataclass(frozen=True, slots=True)
class AuthorLivePreview:
    """Latest received record and bounded durable preview for an acquisition.

    Progress and records are successive reads, not an atomic snapshot. This is
    always a provisional preview, never a sealed dataset or analysis input.
    """

    progress: ProcedureOperatorView
    durable: MeasurementPreview | None
    live: MeasurementLivePreview | None

    @property
    def latest(self) -> MeasurementRecord | None:
        """Latest received record, potentially not yet persisted."""
        return None if self.live is None else self.live.latest

    @property
    def provisional(self) -> Literal[True]:
        return True


@dataclass(frozen=True, slots=True)
class AuthorJob:
    """A locally retained request identity, connected to one session.

    The receipt is written and flushed before submission. It contains the exact
    reviewed request, not result data or a second procedure database. Reopening
    only queries the daemon's existing request-key index; it never submits.
    """

    client: AuthorProject
    receipt: Path
    request: LaunchRequest

    @classmethod
    def retain(
        cls, client: AuthorProject, receipt: Path, request: LaunchRequest
    ) -> AuthorJob:
        receipt.parent.mkdir(parents=True, exist_ok=True)
        with receipt.open("x", encoding="utf-8") as stream:
            stream.write(request.model_dump_json())
            stream.flush()
            os.fsync(stream.fileno())
        return cls(client, receipt, request)

    def reconnect(self, session: AuthorProject) -> AuthorJob:
        """Attach this receipt to a new open session without submission."""
        return session.reopen(self.receipt)

    def recover(self, *, timeout: float | None = None) -> ProcedureRun | None:
        """Read the original admission, or None if not yet found. Never retry."""
        page = self.client.list_procedures(
            ProcedureRunListQuery(request_key=self.request.request_key, limit=1),
            timeout=timeout,
        )
        return page.items[0] if page.items else None

    @property
    def snapshot(self) -> ProcedureRun:
        snapshot = self.recover()
        if snapshot is None:
            raise AuthorSubmissionUncertain(self)
        return snapshot

    @property
    def id(self) -> str:
        return self.snapshot.procedure_run_id

    def progress(self, *, cursor: int | None = None) -> ProcedureOperatorView:
        """Observe dispatch, the exact current step/run and bounded history.

        Reopening uses the original receipt, including for resumed procedures.
        History has at most 50 attempts; pass steps.next_cursor for another page.
        The current child is independent of that history cursor.
        """
        return self.client.procedure_progress(self.id, cursor=cursor)

    def preview(self, *, limit: int = 100) -> AuthorLivePreview:
        """Read the latest received record and up to 100 durable records.

        No current child means durable/live are None (including after completion).
        Before the dataset is initialized live is None; latest may be absent.
        Every response names its procedure, step/attempt and run; calls may observe a
        later step. Use result(step=...) for a retained successful acquisition.
        """
        if not 1 <= limit <= 100:
            raise ValueError("preview limit must be between 1 and 100")
        progress = self.progress()
        child = progress.current_child
        measurements = (
            None
            if child is None
            else self.client.measurement_preview(child.run.run_id, limit=limit)
        )
        live = (
            self.client.measurement_live_preview(
                child.run.run_id, dataset_schema=measurements.dataset_schema
            )
            if child is not None
            and measurements is not None
            and measurements.dataset_schema is not None
            else None
        )
        return AuthorLivePreview(progress, measurements, live)

    def wait(self, *, timeout: float = 60, interval: float = 0.2) -> AuthorJob:
        """Wait up to timeout seconds; attention and cancellation are distinct.

        A timeout leaves execution alone. Polling requests are capped by the
        remaining wait budget, rather than the session's longer launch deadline.
        """
        if timeout < 0 or interval <= 0:
            raise ValueError("timeout must be nonnegative and interval positive")
        deadline = time.monotonic() + timeout
        while True:
            try:
                snapshot = self.recover(timeout=max(0.001, deadline - time.monotonic()))
            except httpx2.TimeoutException as error:
                raise AuthorJobTimeout(
                    "Wait ended; execution was not cancelled"
                ) from error
            if snapshot is None:
                raise AuthorSubmissionUncertain(self)
            if snapshot.state in {"attention_required", "waiting_for_input"}:
                raise AuthorJobAttention(
                    snapshot.attention_reason or snapshot.state, job=self
                )
            if snapshot.closure is not None:
                if snapshot.closure.status == "cancelled":
                    raise AuthorJobCancelled(snapshot.closure.reason)
                if snapshot.closure.status == "failed":
                    raise AuthorJobFailed(snapshot.closure.reason)
                return self
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AuthorJobTimeout(
                    f"Wait ended for {snapshot.procedure_run_id}; execution continues."
                )
            time.sleep(min(interval, remaining))

    def cancel(self, *, reason: str = "Cancelled by author") -> ProcedureRun:
        """Request a stop after the current step settles."""
        snapshot = self.snapshot
        return self.client.cancel_procedure(
            ProcedureCancelCommand(
                procedure_run_id=snapshot.procedure_run_id,
                expected_run_revision=snapshot.revision,
                actor=self.request.actor,
                reason=reason,
            )
        ).run

    def result(self, *, step: str = "experiment") -> RunHandle:
        """Open a retained successful acquisition, even if later analysis failed.

        Lazy dataset reads require this session. Materialized arrays/tables and
        run snapshots remain ordinary local values after it closes. Use
        reconnect(new_session).result() to read more retained data.
        """
        cursor: int | None = None
        procedure_id = self.id
        while True:
            page = self.client.list_procedure_step_attempts(
                procedure_id, ProcedureStepAttemptListQuery(limit=200, cursor=cursor)
            )
            for attempt in page.items:
                if attempt.step_key == step:
                    if isinstance(attempt.output, RunOutputRef):
                        return self.client.run(attempt.output.run_id)
                    raise RuntimeError(f"Step {step!r} has no retained acquisition")
            if page.next_cursor is None:
                raise KeyError(f"Procedure has no step {step!r}")
            cursor = page.next_cursor


def _control_value(value: SupportsFloat | Quantity) -> float | Quantity:
    if isinstance(value, Quantity):
        return value
    if isinstance(value, bool):
        raise TypeError("controls require numeric values, not booleans")
    return float(value)
