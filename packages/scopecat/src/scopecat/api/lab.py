"""High-level notebook client for one daemon-owned lab project."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import TracebackType
from typing import Literal, Self

from scopecat.api._config import LabConfigOperations
from scopecat.api._control import LabControlOperations
from scopecat.api._remote import RemoteRunOperations
from scopecat.api._runner import _DaemonRunner
from scopecat.api.analysis import AnalysisContext, AnalysisStep
from scopecat.api.calibration_planner import CalibrationPlanningContext
from scopecat.api.calibration_policy import CalibrationPublicationPolicyRegistry
from scopecat.api.calibrations import LabCalibrationOperations
from scopecat.api.instruments import LabInstrumentOperations
from scopecat.api.procedure_planner import ProcedurePlanningContext
from scopecat.api.procedures import LabProcedureOperations
from scopecat.api.project_analysis import RemoteProjectAnalysisOperations
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.review import ExperimentReviewHandle
from scopecat.api.run import RunHandle, RunHandlePage, run_handle_id
from scopecat.api.samples import LabSampleOperations, SampleHandle, SampleOperations
from scopecat.authoring.experiments import Experiment, ExperimentInvocation
from scopecat.automation import ProcedureRegistry, ProcedureScheduleRegistry
from scopecat.automation.calibration_definition import CalibrationRegistry
from scopecat.config.candidates import CandidateConfig
from scopecat.control.models import ControlRunState
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import DaemonHealth, ProjectAnalysisPage, SampleAnalysisPage
from scopecat.inspection import CompiledProgramInspectionQuery
from scopecat.planning.preview import PreviewCoordinateMode
from scopecat.planning.preview_models import ExperimentPreview
from scopecat.planning.system import ExperimentSystemBuilder
from scopecat.program.values import MetadataValue
from scopecat.records.analysis import SampleAnalysisSubject
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.run import RunConfigSource
from scopecat.records.sample import SampleSelector
from scopecat.runs.selectors import RunSelector

type ExperimentSpec = ExperimentInvocation | Experiment[...]
type PreviewPoint = int | Literal["first", "middle", "last"]
type SampleSpec = str | SampleHandle | SampleSelector


@dataclass(frozen=True, slots=True)
class PreparedLabExperiment:
    """A config-bound invocation ready for local planning and daemon execution."""

    lab: LabClient
    invocation: ExperimentInvocation
    config: ConfigProfileSnapshot
    config_source: RunConfigSource | None = None

    def preview(
        self,
        *,
        point: PreviewPoint = "first",
        coordinates: Mapping[str, object] | None = None,
        coordinate_mode: PreviewCoordinateMode = "exact",
        inspection_query: CompiledProgramInspectionQuery | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        sample: SampleSpec | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ExperimentPreview:
        return self.lab.preview_invocation(
            self.invocation,
            config=self.config,
            point=point,
            coordinates=coordinates,
            coordinate_mode=coordinate_mode,
            inspection_query=inspection_query,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            sample=sample,
            samples=samples,
        )

    def run(
        self,
        *,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        sample: SampleSpec | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> RunHandle:
        return self.lab.execute_invocation(
            self.invocation,
            config=self.config,
            config_source=self.config_source,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            sample=sample,
            samples=samples,
        )

    def review(
        self,
        *,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        sample: SampleSpec | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ExperimentReviewHandle:
        return self.lab.review_invocation(
            self.invocation,
            config=self.config,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            sample=sample,
            samples=samples,
        )


class LabClient:
    """Notebook operations backed by one daemon HTTP owner."""

    def __init__(
        self,
        daemon: str | DaemonClient,
        *,
        build_experiment_system: ExperimentSystemBuilder | None = None,
        config: ConfigProfileSnapshot | None = None,
        procedures: ProcedureRegistry | None = None,
        procedure_schedules: (
            ProcedureScheduleRegistry[ProcedurePlanningContext] | None
        ) = None,
        calibrations: CalibrationRegistry[CalibrationPlanningContext] | None = None,
        calibration_publications: CalibrationPublicationPolicyRegistry | None = None,
        operator: str = "operator",
    ) -> None:
        self._owns_client = isinstance(daemon, str)
        self._client = DaemonClient(daemon) if isinstance(daemon, str) else daemon
        self._runs = RemoteRunOperations(self._client)
        self._analyses = RemoteProjectAnalysisOperations(self._client)
        self._config = LabConfigOperations(
            client=self._client,
            runs=self._runs,
            default_config=config,
            operator=operator,
        )
        self._control = LabControlOperations(self._client)
        self._instruments = LabInstrumentOperations(
            self._client,
            operator=operator,
        )
        self._samples = LabSampleOperations(
            client=self._client,
            session=self,
            operator=operator,
        )
        self._runner = _DaemonRunner(self._client, build_experiment_system)
        self._procedures = LabProcedureOperations(
            client=self._client,
            runner=self._runner,
            config=self._config,
            session=self,
            registry=procedures if procedures is not None else ProcedureRegistry(),
            schedule_registry=(
                procedure_schedules
                if procedure_schedules is not None
                else ProcedureScheduleRegistry()
            ),
        )
        self._calibrations = LabCalibrationOperations(
            client=self._client,
            config=self._config,
            procedures=self._procedures,
            publication_session=self,
            registry=(
                calibrations if calibrations is not None else CalibrationRegistry()
            ),
            publication_registry=(
                calibration_publications
                if calibration_publications is not None
                else CalibrationPublicationPolicyRegistry()
            ),
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        self.close()

    @property
    def is_closed(self) -> bool:
        """Whether the underlying daemon connection is closed."""

        return self._client.is_closed

    def close(self) -> None:
        """Close an owned connection; a supplied DaemonClient remains caller-owned.

        Live handles and lazy datasets require that connection. Values explicitly
        loaded before closing, including run snapshots, remain ordinary local data.
        """

        if self._owns_client:
            self._client.close()

    @property
    def run_operations(self) -> RemoteRunOperations:
        return self._runs

    @property
    def config(self) -> LabConfigOperations:
        return self._config

    @property
    def control(self) -> LabControlOperations:
        return self._control

    @property
    def instruments(self) -> LabInstrumentOperations:
        return self._instruments

    @property
    def samples(self) -> LabSampleOperations:
        return self._samples

    @property
    def sample_operations(self) -> SampleOperations:
        return self._samples

    @property
    def procedures(self) -> LabProcedureOperations:
        return self._procedures

    @property
    def calibrations(self) -> LabCalibrationOperations:
        return self._calibrations

    def health(self) -> DaemonHealth:
        return self._control.health()

    def runs(
        self,
        *,
        limit: int = 50,
        before: int | None = None,
        state: ControlRunState | None = None,
        sample: SampleSpec | None = None,
    ) -> RunHandlePage:
        """Load one bounded newest-first page of run handles."""

        page = self._control.runs(
            limit=limit,
            before=before,
            state=state,
            sample_id=None if sample is None else _sample_id(sample),
        )
        return RunHandlePage(
            items=tuple(RunHandle(session=self, id=item.run_id) for item in page.items),
            next_cursor=page.next_cursor,
        )

    def analysis(
        self,
        title: str,
        *,
        key: str | None = None,
        sample: SampleSpec | None = None,
    ) -> AnalysisContext:
        """Start a project or sample publication over explicit completed runs."""

        return AnalysisContext(
            owner=self._analysis_owner(sample),
            default_title=title,
            default_key=key,
        )

    def analyze(
        self,
        step: AnalysisStep,
        *,
        key: str | None = None,
        sample: SampleSpec | None = None,
    ) -> PublishedAnalysis:
        """Run and durably publish one project-level analysis step."""

        analysis = step.run(
            AnalysisContext(
                owner=self._analysis_owner(sample),
                default_title=step.id,
                default_key=key or step.id,
                step_id=step.id,
            )
        )
        if analysis.key is None or analysis.step_id is None:
            analysis = replace(
                analysis,
                key=analysis.key or key or step.id,
                step_id=analysis.step_id or step.id,
            )
        return analysis.save()

    def analysis_summaries(
        self,
        *,
        limit: int = 100,
        before: int | None = None,
        sample: SampleSpec | None = None,
    ) -> ProjectAnalysisPage | SampleAnalysisPage:
        """Load a bounded history page without fetching publication bodies."""

        return self._analysis_owner(sample).summaries(limit=limit, before=before)

    def published_analysis(
        self,
        selector: str,
        *,
        sample: SampleSpec | None = None,
    ) -> PublishedAnalysis:
        return self._analysis_owner(sample).published_analysis(selector)

    def _analysis_owner(
        self,
        sample: SampleSpec | None,
    ) -> RemoteProjectAnalysisOperations:
        if sample is None:
            return self._analyses
        return RemoteProjectAnalysisOperations(
            self._client,
            subject=SampleAnalysisSubject(sample_id=_sample_id(sample)),
        )

    def get_run(self, run: RunSelector | RunHandle) -> RunHandle:
        """Attach a live handle to a retained run without executing acquisition.

        Pass the ID saved from a previous session (or its handle). Reads use this
        connection; the previous handle keeps its original connection lifetime.
        """

        run_id = run_handle_id(run)
        self._control.run_detail(run_id)
        return RunHandle(session=self, id=run_id)

    def resume(
        self,
        run: RunSelector | RunHandle,
        experiment: ExperimentSpec,
        *,
        executor_id: str = "notebook",
    ) -> RunHandle:
        """Continue an existing run after externally reconciling its hardware.

        The invocation is planned again against the run's accepted config and
        must reproduce its durable request and run contract. Calling this on an
        attention-required run explicitly authorizes a new execution segment;
        no Git workspace or Python-environment equality is assumed.
        """

        run_id = run_handle_id(run)
        snapshot = self._runner.resume(
            _experiment_invocation(experiment),
            run_id=run_id,
            executor_id=executor_id,
        )
        return RunHandle(session=self, id=snapshot.run_id)

    def resolve_config(
        self,
        config: str | ConfigProfileSnapshot | CandidateConfig | None = None,
    ) -> ConfigProfileSnapshot:
        return self._config.resolve(config)

    def prepare(
        self,
        experiment: ExperimentSpec,
        *,
        config: str | ConfigProfileSnapshot | CandidateConfig | None = None,
    ) -> PreparedLabExperiment:
        invocation = _experiment_invocation(experiment)
        resolved_config, config_source = self._config.resolve_with_source(config)
        return PreparedLabExperiment(
            lab=self,
            invocation=invocation,
            config=resolved_config,
            config_source=config_source,
        )

    def preview(
        self,
        experiment: ExperimentSpec,
        *,
        config: str | ConfigProfileSnapshot | CandidateConfig | None = None,
        point: PreviewPoint = "first",
        coordinates: Mapping[str, object] | None = None,
        coordinate_mode: PreviewCoordinateMode = "exact",
        inspection_query: CompiledProgramInspectionQuery | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        sample: SampleSpec | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ExperimentPreview:
        """Preview an experiment without requiring an explicit prepare step."""

        return self.prepare(experiment, config=config).preview(
            point=point,
            coordinates=coordinates,
            coordinate_mode=coordinate_mode,
            inspection_query=inspection_query,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            sample=sample,
            samples=samples,
        )

    def run(
        self,
        experiment: ExperimentSpec,
        *,
        config: str | ConfigProfileSnapshot | CandidateConfig | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        sample: SampleSpec | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> RunHandle:
        """Run an experiment directly; use ``prepare`` when reusing a config."""

        return self.prepare(experiment, config=config).run(
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            sample=sample,
            samples=samples,
        )

    def review(
        self,
        experiment: ExperimentSpec,
        *,
        config: str | ConfigProfileSnapshot | CandidateConfig | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        sample: SampleSpec | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ExperimentReviewHandle:
        """Open a live GUI backed by this process's pure compiler."""

        return self.prepare(experiment, config=config).review(
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            sample=sample,
            samples=samples,
        )

    def preview_invocation(
        self,
        invocation: ExperimentInvocation,
        *,
        config: ConfigProfileSnapshot,
        point: PreviewPoint = "first",
        coordinates: Mapping[str, object] | None = None,
        coordinate_mode: PreviewCoordinateMode = "exact",
        inspection_query: CompiledProgramInspectionQuery | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        sample: SampleSpec | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ExperimentPreview:
        return self._runner.preview(
            invocation,
            config=config,
            point=point,
            coordinates=coordinates,
            coordinate_mode=coordinate_mode,
            inspection_query=inspection_query,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            samples=_sample_selectors(sample, samples),
        )

    def execute_invocation(
        self,
        invocation: ExperimentInvocation,
        *,
        config: ConfigProfileSnapshot,
        config_source: RunConfigSource | None = None,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        submission_id: str | None = None,
        sample: SampleSpec | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> RunHandle:
        manifest = self._runner.run(
            invocation,
            config=config,
            config_source=config_source,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            submission_id=submission_id,
            samples=_sample_selectors(sample, samples),
        )
        return RunHandle(session=self, id=manifest.run_id)

    def review_invocation(
        self,
        invocation: ExperimentInvocation,
        *,
        config: ConfigProfileSnapshot,
        name: str | None = None,
        tags: tuple[str, ...] = (),
        description: str | None = None,
        metadata: Mapping[str, MetadataValue] | None = None,
        operator: str | None = None,
        sample: SampleSpec | None = None,
        samples: tuple[SampleSelector, ...] = (),
    ) -> ExperimentReviewHandle:
        return self._runner.review(
            invocation,
            config=config,
            name=name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            samples=_sample_selectors(sample, samples),
        )


def _experiment_invocation(experiment: ExperimentSpec) -> ExperimentInvocation:
    return experiment.bind() if isinstance(experiment, Experiment) else experiment


def _sample_selectors(
    sample: SampleSpec | None,
    samples: tuple[SampleSelector, ...],
) -> tuple[SampleSelector, ...]:
    if sample is None:
        return samples
    if samples:
        raise ValueError("sample and samples cannot be combined")
    if isinstance(sample, SampleSelector):
        return (sample,)
    if isinstance(sample, SampleHandle):
        return (sample.selector(),)
    return (SampleSelector(sample_id=sample),)


def _sample_id(sample: SampleSpec) -> str:
    if isinstance(sample, SampleSelector):
        return sample.sample_id
    if isinstance(sample, SampleHandle):
        return sample.id
    return sample


__all__ = [
    "ExperimentReviewHandle",
    "LabClient",
    "PreparedLabExperiment",
]
