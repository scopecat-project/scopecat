from __future__ import annotations

from dataclasses import dataclass

import pytest
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ProcedureStepOutputRef,
    RunOutputRef,
)
from scopecat.records.analysis import ProjectAnalysisSubject, RunAnalysisSubject
from scopecat.records.config import config_content_hash
from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.run import (
    RunConfigSource,
)
from scopecat.records.sample import SampleBinding

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT, bootstrap_config
from reference_lab.workflows.drag_beta_experiment import DragBetaQubit
from reference_lab.workflows.drag_beta_freshness import drag_beta_freshness_calibration
from reference_lab.workflows.drag_beta_procedure import (
    DragBetaCandidateRejectedError,
    DragBetaVerificationIntent,
    drag_beta_verification_procedure,
)
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_MINIMUM_IMPROVEMENT,
    DragBetaVerification,
)


def test_application_registers_exact_drag_beta_procedure_source() -> None:
    application = create_application(EXAMPLE_ROOT)

    assert (
        application.procedures.resolve(drag_beta_verification_procedure.ref).ref
        == drag_beta_verification_procedure.ref
    )
    assert (
        application.calibrations.resolve(drag_beta_freshness_calibration.ref).ref
        == drag_beta_freshness_calibration.ref
    )


def test_verify_only_drag_beta_member_has_no_config_publish_step() -> None:
    context, baseline, fit, candidate_run, verification, _publication = (
        _procedure_context(accepted=True)
    )

    drag_beta_verification_procedure.run(
        context,
        _verification_intent("q1"),
    )

    assert tuple(call.step_key for call in context.run_calls) == (
        "baseline",
        "candidate",
    )
    assert context.run_analysis_calls == [("fit", baseline)]
    assert context.project_analysis_calls == [
        ("verification", (baseline, candidate_run))
    ]
    assert context.published_refs == [fit, verification]


def test_verify_only_drag_beta_rejection_is_a_known_failed_result() -> None:
    context, _baseline, _fit, _candidate_run, _verification, _publication = (
        _procedure_context(accepted=False)
    )

    with pytest.raises(DragBetaCandidateRejectedError, match="accepted=false"):
        drag_beta_verification_procedure.run(
            context,
            _verification_intent("q0"),
        )


def _verification_intent(qubit: DragBetaQubit) -> DragBetaVerificationIntent:
    initial_config = bootstrap_config()
    return DragBetaVerificationIntent(
        qubit=qubit,
        initial_config=initial_config,
        initial_config_source=ContextRunConfigSource(
            context=ConfigContextRef(
                entry_id="working-point-1",
                content_hash=config_content_hash(initial_config),
            ),
            sample=SampleBinding(
                role="sample",
                sample_id="reference-chip",
                revision=1,
                content_hash="sha256:" + "a" * 64,
                kind="synthetic",
                display_name="Reference chip",
                context_id="parked",
            ),
            content_hash=config_content_hash(initial_config),
        ),
    )


def _procedure_context(
    *,
    accepted: bool,
) -> tuple[
    _RecordingProcedureContext,
    RunOutputRef,
    AnalysisPublicationOutputRef,
    RunOutputRef,
    AnalysisPublicationOutputRef,
    _FakePublishedAnalysis,
]:
    baseline = RunOutputRef(run_id="baseline-run")
    fit = AnalysisPublicationOutputRef(
        subject=RunAnalysisSubject(run_id=baseline.run_id),
        analysis_record_id="fit-analysis-record",
    )
    candidate_run = RunOutputRef(run_id="candidate-run")
    verification = AnalysisPublicationOutputRef(
        subject=ProjectAnalysisSubject(),
        analysis_record_id="verification-analysis-record",
    )
    fit_publication = _FakePublishedAnalysis(
        candidate=_FakeCandidate(proposal_id="drag-beta-fit")
    )
    verification_publication = _FakePublishedAnalysis(
        decision=DragBetaVerification(
            baseline_mean_probability_1=0.02,
            candidate_mean_probability_1=0.01 if accepted else 0.02,
            improvement=0.01 if accepted else 0.0,
            minimum_improvement=DRAG_BETA_MINIMUM_IMPROVEMENT,
            accepted=accepted,
        )
    )
    context = _RecordingProcedureContext(
        baseline=baseline,
        fit=fit,
        candidate_run=candidate_run,
        verification=verification,
        fit_publication=fit_publication,
        verification_publication=verification_publication,
    )
    return (
        context,
        baseline,
        fit,
        candidate_run,
        verification,
        verification_publication,
    )


@dataclass(frozen=True, slots=True)
class _RunCall:
    step_key: str
    config: object
    config_source: RunConfigSource | None
    inputs: tuple[ProcedureStepOutputRef, ...]


@dataclass(frozen=True, slots=True)
class _FakeCandidate:
    proposal_id: str


@dataclass(frozen=True, slots=True)
class _FakeRunHandle:
    id: str


class _FakePublishedAnalysis:
    def __init__(
        self,
        *,
        candidate: object | None = None,
        decision: DragBetaVerification | None = None,
    ) -> None:
        self._candidate = candidate
        self._decision = decision

    def candidate_config(self) -> object:
        if self._candidate is None:
            raise AssertionError("publication has no candidate")
        return self._candidate

    def fact_as(
        self,
        id: str,
        schema: object,
    ) -> DragBetaVerification:
        del schema
        if id != "decision" or self._decision is None:
            raise AssertionError("publication has no verification decision")
        return self._decision


class _RecordingProcedureContext:
    procedure_run_id = "procedure-test"

    def __init__(
        self,
        *,
        baseline: RunOutputRef,
        fit: AnalysisPublicationOutputRef,
        candidate_run: RunOutputRef,
        verification: AnalysisPublicationOutputRef,
        fit_publication: _FakePublishedAnalysis,
        verification_publication: _FakePublishedAnalysis,
    ) -> None:
        self.baseline = baseline
        self.fit = fit
        self.candidate_run = candidate_run
        self.verification = verification
        self._fit_publication = fit_publication
        self._verification_publication = verification_publication
        self.run_calls: list[_RunCall] = []
        self.run_analysis_calls: list[tuple[str, RunOutputRef]] = []
        self.project_analysis_calls: list[
            tuple[str, tuple[ProcedureStepOutputRef, ...]]
        ] = []
        self.published_refs: list[AnalysisPublicationOutputRef] = []

    def run(
        self,
        step_key: str,
        experiment: object,
        *,
        config: object,
        config_source: RunConfigSource | None = None,
        inputs: tuple[ProcedureStepOutputRef, ...] = (),
        name: str | None = None,
        tags: tuple[str, ...] = (),
    ) -> RunOutputRef:
        del experiment, name, tags
        self.run_calls.append(
            _RunCall(
                step_key=step_key,
                config=config,
                config_source=config_source,
                inputs=inputs,
            )
        )
        if step_key == "baseline":
            return self.baseline
        if step_key == "candidate":
            return self.candidate_run
        raise AssertionError(f"unexpected run step: {step_key}")

    def analyze_run(
        self,
        step_key: str,
        run: RunOutputRef,
        analysis: object,
    ) -> AnalysisPublicationOutputRef:
        del analysis
        self.run_analysis_calls.append((step_key, run))
        return self.fit

    def analyze_project(
        self,
        step_key: str,
        analysis: object,
        *,
        inputs: tuple[ProcedureStepOutputRef, ...],
    ) -> AnalysisPublicationOutputRef:
        del analysis
        self.project_analysis_calls.append((step_key, inputs))
        return self.verification

    def run_handle(self, ref: RunOutputRef) -> _FakeRunHandle:
        return _FakeRunHandle(ref.run_id)

    def published_analysis(
        self,
        ref: AnalysisPublicationOutputRef,
    ) -> _FakePublishedAnalysis:
        self.published_refs.append(ref)
        if ref == self.fit:
            return self._fit_publication
        if ref == self.verification:
            return self._verification_publication
        raise AssertionError(f"unexpected analysis reference: {ref}")

    def accept_verified_candidate(self, *_args: object, **_kwargs: object) -> None:
        raise AssertionError("verify-only members must not publish")
