"""Receipt cells and independent verification without publishing shared defaults."""

import shutil
from collections.abc import Generator
from dataclasses import replace
from pathlib import Path

import httpx2
import pytest
import scopecat as sc
from scopecat.analysis.facts import ordinary_result_schema
from scopecat.api.parameter_candidates import (
    ParameterCandidate,
    VerifiedParameterCandidate,
)
from scopecat.api.project_worker import ProjectAutomationWorker
from scopecat.application import LabApplication
from scopecat.automation import (
    AnalysisPublicationOutputRef,
    ParameterBranchPublishOutputRef,
    RunOutputRef,
)
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.daemon.wire import (
    AnalysisParameterProposalOutputPayload,
    AnalysisSaveCommand,
    ParameterBranchPublishCommand,
)
from scopecat.project import load_project
from scopecat.records.analysis import analysis_record_id
from scopecat.records.parameter import TableParameterValue
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.run import (
    AnalysisCandidateRunConfigSource,
    ParameterRunConfigSource,
)
from scopecat.records.sample import SampleRevisionDraft
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT, bootstrap_config
from reference_lab.exploration import exploration_config
from reference_lab.parameters import QubitParameters
from reference_lab.workflows.authored.ordinary_analysis import (
    PeakResult,
    PeakVerification,
)
from reference_lab.workflows.drag_beta_analysis import drag_beta_analysis
from reference_lab.workflows.drag_beta_experiment import drag_beta_experiment
from reference_lab.workflows.drag_beta_verification import (
    DRAG_BETA_VERIFICATION_SCHEMA,
    drag_beta_candidate_verification,
)
from reference_lab.workflows.drag_branch_calibration import (
    JOINT_DRAG_DECISION_SCHEMA,
    DragBranchCalibrationIntent,
    DragBranchCalibrationRejected,
    drag_branch_calibration,
    verify_joint_drag,
)
from reference_lab.workflows.production_drag_gate import production_drag_experiment

from .conftest import ReferenceLabDaemon

pytestmark = pytest.mark.usefixtures("reference_lab_author_imports")


@pytest.fixture
def candidate_daemon(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[ReferenceLabDaemon]:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "candidate-project"
    root.mkdir()
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(
        EXAMPLE_ROOT / "fixtures/equipment_bootstrap.py",
        root / "src/equipment_bootstrap.py",
    )
    (root / "scopecat.toml").write_text(
        (EXAMPLE_ROOT / "scopecat.toml")
        .read_text()
        .replace(
            "reference_lab.application:create_bootstrap",
            "equipment_bootstrap:create_bootstrap",
        )
    )
    project = load_project(root / "scopecat.toml")
    endpoint = start_project(project)
    try:
        yield ReferenceLabDaemon(url=endpoint.base_url, root=root)
    finally:
        stop_project(project)


def test_joint_branch_procedure_recovers_after_restart_and_lost_publish_response(
    candidate_daemon: ReferenceLabDaemon,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = load_project(candidate_daemon.root / "scopecat.toml")
    with create_application(candidate_daemon.root).connect(candidate_daemon.url) as lab:
        config = bootstrap_config()
        parameters = lab.parameters.save(
            name="joint-base",
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
        )
        destination = lab.parameters.create_branch("joint", revision=parameters)
        setup = lab.setup.active()
        registry = lab.config.registry()
        sample = lab.samples.create(
            "joint-chip",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Joint chip"),
        )
        intent = DragBranchCalibrationIntent(
            targets=("q0", "q1"),
            initial=lab.parameters.resolve(parameters),
            destination=destination,
            result_revision_id="joint-accepted",
            actor="test",
        )
        procedure = lab.procedures.submit(
            drag_branch_calibration, intent, request_key="joint", sample=sample.id
        )
        # Stop with the composition retained and both joint checks still missing.
        lab.procedures.resume_snapshot(
            procedure.snapshot, should_yield=lambda: len(procedure.steps().items) >= 5
        )
        assert procedure.state == "ready"
        assert lab.parameters.checkout("joint").head == destination
        before = {run.id for run in lab.runs().items}
        assert len(before) == 2
        procedure_id = procedure.id
    stop_project(project)
    restarted = start_project(project)
    with create_application(candidate_daemon.root).connect(restarted.base_url) as lab:
        procedure = lab.procedures.get(procedure_id)
        original_publish = DaemonClient.publish_parameter_branch
        commands: list[ParameterBranchPublishCommand] = []

        def lose_response(
            client: DaemonClient, command: ParameterBranchPublishCommand
        ) -> ParameterBranch:
            commands.append(command)
            original_publish(client, command)
            raise httpx2.ReadError("committed response lost")

        with monkeypatch.context() as patch:
            patch.setattr(DaemonClient, "publish_parameter_branch", lose_response)
            dispatched = ProjectAutomationWorker(lab.procedures).cycle()
            assert dispatched.procedures.dispatched == 1
        assert procedure.state == "attention_required"
        assert procedure.step("publish").state == "attention_required"
        accepted = lab.parameters.checkout("joint").head
        assert accepted.generation == destination.generation + 1
        run_ids = {run.id for run in lab.runs().items}
        assert len(run_ids) == 4 and before < run_ids
        # An unrelated later edit must not make recovery target today's head.
        advanced = lab.parameters.checkout("joint").save(
            catalog=parameters.catalog,
            parameters=parameters.parameters,
            note="later edit",
        )
        procedure.retry_attention()
        assert procedure.summary().outcome == "succeeded"
        output = procedure.output("publish")
        assert isinstance(output, ParameterBranchPublishOutputRef)
        assert output.branch == accepted
        assert lab.parameters.checkout("joint").head == advanced
        assert {run.id for run in lab.runs().items} == run_ids
        assert (
            lab.procedures.submit(
                drag_branch_calibration, intent, request_key="joint", sample=sample.id
            ).id
            == procedure_id
        )
        assert len(commands) == 1
        assert all(
            lab.get_run(run_id).samples[0].sample_id == sample.id for run_id in run_ids
        )
        verification = procedure.output("verify")
        assert isinstance(verification, AnalysisPublicationOutputRef)
        decision = lab.published_analysis(verification.analysis_record_id).fact_as(
            "decision", JOINT_DRAG_DECISION_SCHEMA
        )
        assert decision.accepted and decision.checked == ("q0", "q1")
        assert decision.missing == decision.rejected == ()
        checks = [procedure.output(f"check-{target}") for target in intent.targets]
        assert all(isinstance(check, RunOutputRef) for check in checks)
        assert lab.config.registry() == registry and lab.setup.active() == setup
        idle = ProjectAutomationWorker(lab.procedures).cycle()
        assert idle.procedures.dispatched == 0
        assert {run.id for run in lab.runs().items} == run_ids
        # An incomplete policy input is retained as a rejection, never a subset success.
        q0_baseline = procedure.output("baseline-q0")
        q0_check = procedure.output("check-q0")
        assert isinstance(q0_baseline, RunOutputRef) and isinstance(
            q0_check, RunOutputRef
        )
        missing = lab.analyze(
            verify_joint_drag(
                targets=("q0", "q1"),
                baselines={"q0": lab.get_run(q0_baseline.run_id)},
                candidates={"q0": lab.get_run(q0_check.run_id)},
                minimum_improvement=0.001,
            )
        ).fact_as("decision", JOINT_DRAG_DECISION_SCHEMA)
        assert missing.missing == ("q1",) and not missing.accepted


@pytest.mark.parametrize("reject", [True, False])
def test_branch_procedure_rejection_and_stale_destination_do_not_publish(
    candidate_daemon: ReferenceLabDaemon,
    reject: bool,
) -> None:
    with create_application(candidate_daemon.root).connect(candidate_daemon.url) as lab:
        config = bootstrap_config()
        parameters = lab.parameters.save(
            name="base",
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
        )
        destination = lab.parameters.create_branch("daily", revision=parameters)
        intent = DragBranchCalibrationIntent(
            targets=("q0",),
            initial=lab.parameters.resolve(parameters),
            destination=destination,
            result_revision_id="accepted",
            actor="test",
            minimum_improvement=1.0 if reject else 0.001,
        )
        procedure = lab.procedures.submit(
            drag_branch_calibration, intent, request_key="calibrate"
        )
        if not reject:
            destination = lab.parameters.checkout("daily").save(
                catalog=parameters.catalog, parameters=parameters.parameters
            )
        with pytest.raises(
            DragBranchCalibrationRejected if reject else DaemonConflictError,
            match="Joint DRAG verification" if reject else "branch changed",
        ):
            procedure.resume()
        assert procedure.summary().outcome == "failed"
        assert lab.parameters.checkout("daily").head == destination
        if reject:
            verification = procedure.output("verify")
            assert isinstance(verification, AnalysisPublicationOutputRef)
            decision = lab.published_analysis(verification.analysis_record_id).fact_as(
                "decision", JOINT_DRAG_DECISION_SCHEMA
            )
            assert decision.rejected == ("q0",) and decision.missing == ()
            assert not any(
                step.operation == "parameter_publish"
                for step in procedure.steps().items
            )


def test_typed_candidates_retain_cells_and_independent_policy(
    candidate_daemon: ReferenceLabDaemon,
) -> None:
    project = load_project(candidate_daemon.root / "scopecat.toml")
    analysis_module = "reference_lab.workflows.authored.ordinary_analysis"
    with (
        LabApplication().connect(candidate_daemon.url) as lab,
        project.authoring() as author,
    ):
        config = exploration_config(sc.Quantity(4.83, "GHz"))
        baseline = lab.parameters.save(
            name="baseline",
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
        )
        branch = lab.parameters.create_branch("daily", revision=baseline)
        registry = lab.config.registry()
        assert registry.entries == () and registry.activation is None
        setup = lab.setup.active()
        sample = lab.samples.create(
            "candidate-sample",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Candidate sample"),
        )
        author.use(sample=sample.id, parameter_branch="daily")
        author.refresh()
        run = (
            author.prepare(
                "signal",
                scans={"frequency": [4.7, 4.8, 4.9, 5.0]},
            )
            .run()
            .wait(timeout=60)
            .result()
        )
        fit = author.analyze_as(run.id, f"{analysis_module}:estimate_peak", PeakResult)
        assert fit.value.frequency is not None
        # The materialized dataclass is never authority, even if mutated locally.
        edited = replace(
            fit, value=replace(fit.value, frequency=sc.Quantity(5.9, "GHz"))
        )
        candidate = author.config.stage(
            edited,
            name="carrier",
            table=QubitParameters,
            key="q0",
            fields={QubitParameters.drive_carrier_frequency: "frequency"},
        )
        manual = (
            run.analysis("manual estimate")
            .result()
            .fact("result", fit.value, schema=ordinary_result_schema(PeakResult))
            .save()
            .result_as(PeakResult)
        )
        with pytest.raises(ValueError, match="no managed author source receipt"):
            author.config.stage(
                manual,
                name="manual",
                table="qubits",
                key="q0",
                fields={"drive_carrier_frequency": "frequency"},
            )
        original = run.config.parameter_snapshot.get("qubits")
        resolved = author.config.resolve(candidate.config).parameter_snapshot.get(
            "qubits"
        )
        assert isinstance(original, TableParameterValue) and isinstance(
            resolved, TableParameterValue
        )
        for before, after in zip(original.rows, resolved.rows, strict=True):
            for field, value in before.items():
                if field != "drive_carrier_frequency":
                    assert after[field] == value
        cells = candidate.config.parameter_proposal.deltas[0].cells
        assert cells is not None and len(cells) == 1
        assert isinstance(cells[0].after, sc.Quantity)
        assert cells[0].after.to("GHz").value == pytest.approx(
            fit.value.frequency.to("GHz").value
        )
        reopened = author.config.candidate(run.id, candidate.name)
        assert reopened.config == candidate.config
        assert lab.config.registry() == registry
        with pytest.raises(ValueError, match="independent retained"):
            candidate.verify(fit)
        unknown = author.analyze_as(
            run.id,
            f"{analysis_module}:estimate_peak",
            PeakResult,
            arguments={"minimum_contrast": 2.0},
        )
        with pytest.raises(ValueError, match="unknown"):
            author.config.stage(
                unknown,
                name="unknown",
                table="qubits",
                key="q0",
                fields={"drive_carrier_frequency": "frequency"},
            )
        prepared = author.prepare(
            "signal", candidate=reopened, scans={"frequency": [4.7, 4.8, 4.9, 5.0]}
        )
        check_run = prepared.run().wait(timeout=60).result()
        assert isinstance(
            check_run.snapshot.config_source, AnalysisCandidateRunConfigSource
        )
        assert check_run.samples == run.samples
        assert check_run.id != run.id
        check = author.analyze_as(
            check_run.id,
            f"{analysis_module}:verify_peak",
            PeakVerification,
            arguments={
                "expected_frequency": sc.Quantity(4.8, "GHz"),
                "tolerance": sc.Quantity(0.05, "GHz"),
            },
        )
        other = author.config.stage(
            fit,
            name="other-carrier",
            table="qubits",
            key="q0",
            fields={"drive_carrier_frequency": "frequency"},
        )
        with pytest.raises(ValueError, match="exact candidate"):
            other.verify(check)
        verified = candidate.verify(check)
        assert lab.parameters.checkout("daily").head == branch
        next_prepared = author.prepare("signal", candidate=verified.select())
        assert (
            next_prepared.preview.reviewed.config_source
            == prepared.preview.reviewed.config_source
        )
        assert lab.config.registry() == registry
        publication_branch = lab.parameters.create_branch("accepted", revision=baseline)
        published = verified.publish_to_branch(
            publication_branch,
            name="verified-carrier",
            note="Independent policy passed",
        )
        assert (
            verified.publish_to_branch(
                publication_branch,
                name="verified-carrier",
                note="Independent policy passed",
            )
            == published
        )
        assert published.publication is not None
        assert (
            published.publication.verification.analysis_record_id
            == verified.verification.id
        )
        assert (
            lab.parameters.get(published.revision.revision_id).parameters
            == check_run.config.parameter_snapshot
        )
        # Editing the branch does not change an already verified candidate.
        author.params["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(
            5.0, "GHz"
        )
        advanced = author.params.save(note="Independent manual edit")
        retained = next_prepared.run().wait(timeout=60).result()
        assert retained.config == check_run.config
        assert (
            retained.snapshot.config_source == prepared.preview.reviewed.config_source
        )

        assert lab.config.registry() == registry
        assert lab.setup.active() == setup
        assert lab.parameters.get(baseline.id) == baseline
        assert lab.parameters.checkout("daily").head.generation == branch.generation + 1
        assert lab.parameters.checkout("daily").head.revision == advanced.ref


def test_drag_candidate_publishes_to_branch_and_runs_accepted_gate(
    candidate_daemon: ReferenceLabDaemon,
) -> None:
    with create_application(candidate_daemon.root).connect(candidate_daemon.url) as lab:
        config = bootstrap_config()
        parameters = lab.parameters.save(
            name="drag-baseline",
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
        )
        branch = lab.parameters.create_branch("drag/daily", revision=parameters)
        registry = lab.config.registry()
        assert registry.entries == () and registry.activation is None
        setup = lab.setup.active()
        resolved = lab.parameters.resolve(parameters, setup=setup.revision)
        invocation = drag_beta_experiment.build()
        baseline = lab.run(invocation, config=resolved)
        assert baseline.status == "completed"
        assert len(baseline.measurements().records) == 15
        analysis = baseline.analyze(drag_beta_analysis())
        [proposal] = analysis.parameter_proposals
        assert proposal.evidence_output_ids == ("quadratic-fit", "observations")
        assert analysis.artifact("fit-report").entry.filename == "drag-beta-fit.md"
        figure = analysis.figure("observations-by-amplification")
        assert [layer.id for layer in figure.layers] == ["measured", "fit"]
        assert [layer.preview.kind for layer in figure.layers] == ["scatter", "line"]
        assert figure.layers[0].total_points == 15
        assert figure.layers[1].total_points == 243
        uncertainty = figure.layers[1].projection.uncertainty
        assert uncertainty is not None
        assert "not a confidence interval" in uncertainty.meaning
        assert all(
            series.y_lower is not None for series in figure.layers[1].preview.series
        )

        candidate = analysis.candidate_config()
        check = lab.run(invocation, config=candidate)
        assert check.status == "completed"
        source = check.snapshot.config_source
        assert isinstance(source, AnalysisCandidateRunConfigSource)
        assert source.analysis_record_id == analysis.id
        assert source.proposal_id == candidate.proposal_id
        verification = lab.analyze(
            drag_beta_candidate_verification(baseline_run=baseline, candidate_run=check)
        )
        decision = verification.fact_as("decision", DRAG_BETA_VERIFICATION_SCHEMA)
        assert decision.accepted and decision.improvement >= 0.001
        assert verification.view.analysis.subject.kind == "project"
        assert [(item.id, item.role) for item in verification.inputs] == [
            ("baseline", "baseline"),
            ("candidate", "candidate"),
        ]
        assert verification.artifact("verification-report").entry.filename == (
            "drag-beta-verification.md"
        )
        assert all(
            entry.id != verification.id
            for run in (baseline, check)
            for entry in run.contents(role="record").items
        )
        assert lab.config.registry() == registry
        assert lab.setup.active() == setup
        assert lab.parameters.checkout("drag/daily").head == branch
        assert lab.parameters.get(parameters.id) == parameters
        # This legacy numerical analysis publishes its own retained decision;
        # the server validates that evidence again at the publication boundary.
        verified = VerifiedParameterCandidate(
            ParameterCandidate(lab.config, candidate), verification
        )
        published = verified.publish_to_branch(branch, name="drag-accepted")
        assert published.publication is not None
        assert published.publication.verification.analysis_record_id == verification.id
        accepted = lab.parameters.get(published.revision.revision_id)
        assert accepted.parameters == check.config.parameter_snapshot
        production = lab.run(
            production_drag_experiment.build(),
            config=lab.parameters.resolve(accepted, setup=setup.revision),
        )
        assert production.status == "completed"
        assert len(production.measurements().records) == 1
        production_source = production.snapshot.config_source
        assert isinstance(production_source, ParameterRunConfigSource)
        assert production_source.parameters == published.revision
        assert production_source.setup == setup.revision.ref
        assert lab.parameters.checkout("drag/daily").head == published
        assert lab.config.registry() == registry
        assert lab.setup.active() == setup
        assert lab.parameters.get(parameters.id) == parameters

        # Fit another target from the same saved base, then verify the combination.
        second_baseline = lab.run(drag_beta_experiment.build("q1"), config=resolved)
        second_analysis = second_baseline.analyze(drag_beta_analysis(qubit="q1"))
        joint = ParameterCandidate(lab.config, candidate).combine(
            ParameterCandidate(lab.config, second_analysis.candidate_config()),
            name="joint-drag",
        )
        provenance = joint.config.parameter_proposal.composition
        assert provenance is not None and provenance.base == parameters.ref
        assert {item.run_id for item in provenance.sources} == {
            baseline.id,
            second_baseline.id,
        }
        assert lab.config.candidate(baseline.id, joint.name).config == joint.config
        first = ParameterCandidate(lab.config, candidate)
        second = ParameterCandidate(lab.config, second_analysis.candidate_config())
        assert first.combine(second, name="joint-drag").config == joint.config
        with pytest.raises(DaemonConflictError, match="original candidates"):
            joint.combine(second, name="nested")
        with pytest.raises(DaemonConflictError, match="unique"):
            first.combine(first, name="duplicate-contribution")
        tampered = replace(
            second,
            config=replace(
                second.config,
                parameter_proposal=second.config.parameter_proposal.model_copy(
                    update={"reason": "altered locally"}
                ),
            ),
        )
        with pytest.raises(DaemonConflictError, match="retained proposal"):
            first.combine(tampered, name="tampered-source")
        before = baseline.contents(role="record").items
        forged = joint.config.parameter_proposal.model_copy(
            update={
                "id": "forged",
                "analysis_record_id": analysis_record_id("forged", 1),
                "deltas": candidate.parameter_proposal.deltas,
            }
        )
        with pytest.raises(DaemonConflictError, match="merged values"):
            lab.config.client.save_analysis(
                baseline.id,
                AnalysisSaveCommand(
                    title="forged",
                    analysis_key="forged",
                    outputs=(
                        AnalysisParameterProposalOutputPayload(
                            kind="parameter_change_proposal",
                            id="forged",
                            title="forged",
                            content=forged,
                        ),
                    ),
                ),
            )
        assert baseline.contents(role="record").items == before
        joint_branch = lab.parameters.create_branch("drag/joint", revision=parameters)
        with pytest.raises(DaemonConflictError):
            VerifiedParameterCandidate(joint, verification).publish_to_branch(
                joint_branch,
                name="not-jointly-verified",
            )
        q0_check = lab.run(invocation, config=joint.config)
        q1_check = lab.run(drag_beta_experiment.build("q1"), config=joint.config)
        joint_verification = lab.analyze(
            verify_joint_drag(
                targets=("q0", "q1"),
                baselines={"q0": baseline, "q1": second_baseline},
                candidates={"q0": q0_check, "q1": q1_check},
                minimum_improvement=0.001,
            )
        )
        joint_published = VerifiedParameterCandidate(
            joint, joint_verification
        ).publish_to_branch(
            joint_branch,
            name="joint-drag-accepted",
        )
        assert (
            lab.parameters.get(joint_published.revision.revision_id).parameters
            == q0_check.config.parameter_snapshot
        )
        assert q1_check.config == q0_check.config
        assert lab.config.registry() == registry
        assert lab.setup.active() == setup
