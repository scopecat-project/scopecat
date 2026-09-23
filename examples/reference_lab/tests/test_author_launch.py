"""Copy an ordinary author file, edit it, then use Python and real HTTP launch."""

from __future__ import annotations

import shutil
import time
from collections.abc import Generator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Literal, cast

import httpx2
import numpy as np
import pytest
import scopecat as sc
from scopecat.api.analysis import AnalysisDefinition, AnalysisStep
from scopecat.application import LabApplication
from scopecat.application.author_project import AuthorProject
from scopecat.application.launch import LaunchCatalog, LaunchPreview, LaunchSubmission
from scopecat.daemon.client import DaemonClient
from scopecat.kernel.frozen import thaw_json_value
from scopecat.kernel.quantity import Quantity
from scopecat.project import load_project
from scopecat.records.control_edit import ControlEdit
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.run_request import AxisValuesSourceRecord
from scopecat.records.sample import SampleRevisionDraft
from scopecat.records.scientific_selection import (
    ParameterConfiguration,
    SampleSubjectChoice,
    ScientificSelection,
)
from scopecat_server.author_worker import revision_project
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_testkit.project_loading import isolated_project_imports

from reference_lab.configuration import EXAMPLE_ROOT, bootstrap_config


@dataclass(frozen=True)
class AuthorDaemon:
    url: str
    application: LabApplication
    source: str
    analysis: AnalysisStep
    root: Path
    configuration: ParameterConfiguration


@pytest.fixture(scope="module")
def reference_lab_daemon(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[AuthorDaemon]:
    root = tmp_path_factory.mktemp("ordinary-author")
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
    source_path = root / "src/reference_lab/workflows/authored/signal.py"
    source = source_path.read_text(encoding="utf-8")
    # Only ordinary author code changes, with no Git repository or project edits.
    source = (
        source.replace("def signal(", "def copied_signal(")
        .replace('"Exploratory signal"', '"Copied signal"')
        .replace("return gain /", "return 2 * gain /")
        .replace('sc.Quantity(48, "ns")', 'sc.Quantity(88, "ns")')
        .replace(".with_shots(8)", ".with_shots(4)")
        .replace(
            '.fact("mean", float(selected.mean()))',
            '.fact("mean", float(selected.max()))',
        )
    )
    source += (
        "\n@sc.experiment\n"
        "def required_target(context: sc.ExperimentContext, qubit: str) -> None:\n"
        "    del context, qubit\n"
        "\n@sc.experiment\n"
        "def required_level(context: sc.ExperimentContext, "
        "level: Annotated[sc.Input[float], sc.ControlSpec(scannable=True)]) "
        "-> sc.Input[float]:\n"
        "    return level\n"
    )
    # Hold a later compute batch until the observer has read the first one.
    source += f"""

def preview_value(frequency: sc.Quantity) -> float:
    from pathlib import Path
    import time
    if frequency.to("GHz").value > 4.75:
        release = Path({str(root / "release-preview")!r})
        deadline = time.monotonic() + 60
        while not release.exists():
            if time.monotonic() > deadline:
                raise TimeoutError("Preview observer did not release virtual point")
            time.sleep(0.02)
    return float(frequency.to("GHz").value)

@sc.experiment
def preview_signal(
    experiment: sc.ExperimentContext,
    frequency: Annotated[sc.Input[sc.Quantity], sc.ControlSpec(scannable=True)]
        = sc.Quantity(4.7, "GHz"),
) -> sc.ValueRef[float]:
    return experiment.compute(fn=preview_value, frequency=frequency)
"""
    source_path.write_text(source, encoding="utf-8")
    project = load_project(root / "scopecat.toml")
    with isolated_project_imports():
        load_project(EXAMPLE_ROOT / "scopecat.toml").load_bootstrap()
    with pytest.MonkeyPatch.context() as patch:
        patch.delenv("SCOPECAT_DAEMON_URL", raising=False)
        endpoint = start_project(project)
        try:
            with DaemonClient(endpoint.base_url, timeout=120) as client:
                active = client.author_revision_state().active
                assert active is not None
            with isolated_project_imports():
                application = revision_project(root, active).load_application()
                analysis = cast(
                    "AnalysisDefinition[...]",
                    import_module(
                        "reference_lab.workflows.authored.signal"
                    ).selected_mean,
                )()
            # Keep the loaded objects, not snapshot import paths, across the
            # function-scoped loader isolation used by the rest of this suite.
            with application.connect(endpoint.base_url) as lab:
                config = bootstrap_config()
                parameters = lab.parameters.save(
                    name="author-inputs",
                    catalog=config.parameter_catalog,
                    parameters=config.parameter_snapshot,
                )
                configuration = ParameterConfiguration(
                    ref=parameters.ref, setup=lab.setup.active().revision.ref
                )
                assert lab.config.registry().entries == ()
            yield AuthorDaemon(
                endpoint.base_url, application, source, analysis, root, configuration
            )
            with application.connect(endpoint.base_url) as lab:
                assert lab.config.registry().entries == ()
                assert lab.setup.active().revision.ref == configuration.setup
        finally:
            stop_project(project)


def test_copied_author_uses_shared_control_plan_and_real_retained_run(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    fixture = reference_lab_daemon
    authors = fixture.application.authors
    provider = fixture.application.launch_provider
    assert authors is not None and provider is not None
    selected = authors.get("copied_signal")
    with (
        fixture.application.connect(fixture.url) as lab,
        httpx2.Client(base_url=fixture.url, timeout=30, trust_env=False) as http,
    ):
        catalog = http.get("/api/v1/experiment-launcher")
        catalog.raise_for_status()
        assert selected.entry in LaunchCatalog.model_validate(catalog.json()).entries
        collection_response = http.put(
            "/api/v1/record-collections/author-cooldown",
            json={"name": "Author cooldown"},
        )
        collection_response.raise_for_status()
        before = lab.parameters.resolve(
            fixture.configuration.ref, setup=fixture.configuration.setup
        )
        chip = lab.samples.create(
            "author-chip",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Author fixture"),
            note="Software author journey",
        )
        retained: list[np.ndarray[tuple[int, ...], np.dtype[np.float64]]] = []
        for mode in ("fixed", "scan"):
            edit = (
                ControlEdit(mode="fixed", value=Quantity(4.8, "GHz"))
                if mode == "fixed"
                else ControlEdit(
                    mode="scan",
                    axis=AxisValuesSourceRecord(values=[Quantity(4800, "MHz")]),
                )
            )
            request = LaunchRequest(
                action="preview",
                experiment=selected.entry.id,
                version=selected.entry.version,
                control_edits={"frequency": edit},
                inputs={"polarity": "negative"},
                actor="ordinary-author",
                selection=ScientificSelection(
                    subject=SampleSubjectChoice(sample_id=chip.id),
                    configuration=fixture.configuration,
                ),
                record_collection="author-cooldown",
            )
            response = http.post(
                "/api/v1/experiment-launcher/preview",
                json=request.model_dump(mode="json"),
            )
            response.raise_for_status()
            preview = LaunchPreview.model_validate(response.json())
            direct = selected.edit(
                config=before.config, edits=request.control_edits, inputs=request.inputs
            )
            assert (
                preview.point_count
                == lab.preview(direct, config=before.config).initial_point_count
                == 1
            )
            command = LaunchRequest.model_validate(
                {
                    **request.model_dump(),
                    "action": "submit",
                    "request_key": f"author-{mode}",
                    "expected_request_hash": preview.request_hash,
                    "reviewed": preview.reviewed,
                    "code_revision": preview.code_revision,
                    "manual_state": preview.manual_state,
                }
            )
            admitted = provider(lab, command)
            assert isinstance(admitted, LaunchSubmission)
            assert provider(lab, command) == admitted
            procedure = lab.procedures.get(admitted.procedure_id).resume()
            output = procedure.output("experiment")
            assert output.kind == "run"
            run = lab.get_run(output.run_id)
            assert run.status == "completed"
            domain = run.request.point_plan.domain
            assert domain.kind == "grid"
            assert domain.axes[0].mode == mode
            assert run.request.metadata["author_declaration"] == dict(selected.source)
            assert run.request.metadata["author_fingerprint"] == selected.fingerprint
            assert "def copied_signal" in selected.source["source"]
            assert run.request.record_collection == "author-cooldown"
            with AuthorProject(fixture.url) as author:
                address = author.get_run(run.id).address
                assert address is not None
                assert address.number == (1 if mode == "fixed" else 2)
                assert (
                    author.run(address.number, collection="author-cooldown").id
                    == run.id
                )
            assert run.request.operator == "ordinary-author"
            assert run.samples[0].sample_id == chip.id
            assert run.samples[0].role == "subject"
            retained.append(
                np.asarray(
                    run.measurements()["result"].require_values(), dtype=np.float64
                )
            )
        np.testing.assert_array_equal(retained[0], retained[1])
        np.testing.assert_array_equal(retained[0], (-2.0,))
        assert lab.config.registry().entries == ()
        python_run = selected.run(
            lab,
            config=before,
            edits={
                "frequency": sc.axis(
                    next(
                        field.ref
                        for field in selected.controls.fields
                        if field.id == "frequency"
                    ),
                    [Quantity(4.7, "GHz"), Quantity(4.8, "GHz"), Quantity(4.9, "GHz")],
                )
            },
        )
        assert python_run.request.metadata["author_declaration"] == dict(
            selected.source
        )
        fitted = python_run.analyze(fixture.analysis)
        assert fitted.fact("mean").value == 2.0
        wrong = LaunchRequest(
            action="preview",
            experiment="copied_signal",
            selection=ScientificSelection(configuration=fixture.configuration),
            version=selected.entry.version,
            control_edits={
                "frequency": ControlEdit(mode="fixed", value=Quantity(7, "GHz"))
            },
        )
        response = http.post(
            "/api/v1/experiment-launcher/preview", json=wrong.model_dump(mode="json")
        )
        assert response.status_code == 422
        assert "frequency" in response.text
        changed = wrong.model_copy(update={"version": "stale", "control_edits": {}})
        with pytest.raises(ValueError, match="declaration changed"):
            provider(lab, changed)
        branch = lab.parameters.create_branch(
            "author-daily", revision=fixture.configuration.ref
        )
        editor = lab.parameters.workspace(branch.name)
        editor["qubits"]["q0"]["drive_carrier_frequency"] = sc.Quantity(5.1, "GHz")
        editor.save()
        assert provider(lab, command) == admitted
        replay = provider(
            lab, command.model_copy(update={"request_key": "exact-replay"})
        )
        assert isinstance(replay, LaunchSubmission)
        repeated = lab.procedures.get(replay.procedure_id).resume()
        output = repeated.output("experiment")
        assert output.kind == "run"
        assert lab.get_run(output.run_id).config == before.config
        assert lab.config.registry().entries == ()


def test_author_changes_supported_timing_without_application_edits(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    fixture = reference_lab_daemon
    authors = fixture.application.authors
    assert authors is not None
    with fixture.application.connect(fixture.url) as lab:
        config = lab.parameters.resolve(
            fixture.configuration.ref, setup=fixture.configuration.setup
        ).config
        entry = authors.get("ramsey")
        invocation = entry.edit(config=config)
        preview = lab.preview(invocation, config=config)
        assert preview.initial_point_count == 1
        run = lab.run(invocation, config=config)
        assert run.status == "completed"
        domain = run.request.point_plan.domain
        assert domain.kind == "grid"
        assert domain.axes[0].source.kind == "values"
        assert domain.axes[0].source.values == [Quantity(88, "ns")]


def test_revision_aware_notebook_prepare_preserves_parameter_context(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    from scopecat.application.author_project import AuthorProject
    from scopecat.records.run import ParameterRunConfigSource

    from reference_lab.parameters import QubitParameters

    fixture = reference_lab_daemon
    with fixture.application.connect(fixture.url) as lab:
        setup = lab.setup.active()
        sample = lab.samples.create(
            "notebook-context",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Notebook context"),
        )
        overrides = (
            sc.parameter_update(
                QubitParameters.drive_carrier_frequency,
                sc.EntityRef(id="q0", kind="logical_qubit"),
                sc.Quantity(5.1, "GHz"),
            ),
        )
        expected = lab.parameters.resolve(
            fixture.configuration.ref,
            setup=fixture.configuration.setup,
            overrides=overrides,
        )
        with AuthorProject(fixture.url, timeout=120) as authors:
            prepared = authors.prepare(
                "copied_signal",
                selection=ScientificSelection(
                    subject=SampleSubjectChoice(sample_id=sample.id),
                    configuration=fixture.configuration.model_copy(
                        update={"overrides": overrides}
                    ),
                ),
            )
            admitted = prepared.submit(request_key="notebook-context-launch")
        assert prepared.request.selection.configuration.kind == "parameters"
        assert prepared.request.selection.configuration.ref == fixture.configuration.ref
        assert prepared.request.selection.configuration.overrides == overrides
        assert prepared.preview.code_revision is not None
        source = prepared.preview.reviewed.config_source
        assert isinstance(source, ParameterRunConfigSource)
        assert source == expected.config_source
        assert prepared.preview.reviewed.binding.samples[0].sample_id == sample.id
        procedure = lab.procedures.get(admitted.procedure_id)
        deadline = time.monotonic() + 30
        while procedure.state in {"ready", "leased"} and time.monotonic() < deadline:
            time.sleep(0.05)
        assert procedure.state == "closed"
        revision = prepared.preview.code_revision
        assert thaw_json_value(
            procedure.snapshot.intent["code_revision"]
        ) == revision.model_dump(mode="json")
        assert thaw_json_value(
            procedure.snapshot.intent["config_source"]
        ) == source.model_dump(mode="json")
        output = procedure.output("experiment")
        assert output.kind == "run"
        run = lab.get_run(output.run_id)
        assert run.status == "completed"
        assert run.snapshot.config_source == source
        assert run.request.metadata["author_code_revision"] == revision.content_hash
        assert run.samples[0].sample_id == "notebook-context"
        assert lab.setup.active() == setup
        assert lab.config.registry().entries == ()


def test_required_author_input_diagnostics_survive_the_worker_boundary(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    with AuthorProject(reference_lab_daemon.url) as author:
        author.use(
            parameters=reference_lab_daemon.configuration.ref,
            setup=reference_lab_daemon.configuration.setup,
        )
        entry = next(
            item for item in author.catalog().entries if item.id == "required_target"
        )
        assert entry.request.required == ("qubit",)
        with pytest.raises(httpx2.HTTPStatusError, match="qubit: Field required"):
            author.prepare("required_target")
        with pytest.raises(httpx2.HTTPStatusError, match="qubit: Input should be"):
            author.prepare("required_target", inputs={"qubit": 12})
        prepared = author.prepare("required_target", inputs={"qubit": "q0"})
        assert prepared.request.inputs == {"qubit": "q0"}


@dataclass
class SignalInputs:
    gain: float
    frequency: sc.Quantity | sc.Scan
    polarity: Literal["positive", "negative"]


@pytest.mark.parametrize("typed", [False, True])
def test_editable_request_rebuilds_and_reuses_saved_plan(
    typed: bool,
    reference_lab_daemon: AuthorDaemon,
    tmp_path: Path,
) -> None:
    fixture = reference_lab_daemon
    assert fixture.application.authors is not None
    declaration = fixture.application.authors.get("copied_signal").declaration
    request = declaration(gain=1.0)
    request.values["gain"] = 2.0
    frequencies = np.array([4.7, 4.8, 4.9])
    request.values["frequency"] = sc.Scan(
        sc.Quantity(value, "GHz") for value in cast("list[float]", frequencies.tolist())
    )
    frequencies[:] = 5.2
    with AuthorProject(fixture.url, receipts=tmp_path / "receipts") as author:
        author.use(
            parameters=reference_lab_daemon.configuration.ref,
            setup=reference_lab_daemon.configuration.setup,
        )
        selected = request.typed(SignalInputs) if typed else request
        scanned = author.prepare(selected)
        assert scanned.request.control_edits["frequency"].mode == "scan"
        assert scanned.preview.point_count == 3
        alternative = request.copy()
        alternative.values["frequency"] = sc.Quantity(4.8, "GHz")
        alternative.values["polarity"] = "negative"
        if typed:
            typed_alternative = selected.typed(SignalInputs).copy()
            typed_alternative.values.frequency = sc.Quantity(4.8, "GHz")
            typed_alternative.values.polarity = "negative"
            fixed = author.prepare(typed_alternative)
        else:
            fixed = author.prepare(alternative)
        assert fixed.preview.point_count == 1
        assert scanned.request.inputs["polarity"] == "positive"
        assert fixed.request.inputs["polarity"] == "negative"
        saved = scanned.save_plan("Editable request scan", saved_by="alice")
        reopened = author.prepare_plan(saved.ref, actor="bob")
        assert reopened.request.inputs == scanned.request.inputs
        assert reopened.request.control_edits == scanned.request.control_edits
        assert reopened.preview.code_revision == scanned.preview.code_revision
        assert reopened.preview.point_count == 3
        positive = reopened.run().wait(timeout=30).result()
        negative = fixed.run().wait(timeout=30).result()
        assert np.all(
            np.asarray(positive.measurements()["result"].require_values(), dtype=float)
            > 0
        )
        assert np.all(
            np.asarray(negative.measurements()["result"].require_values(), dtype=float)
            < 0
        )
        assert scanned.request.control_edits["frequency"].mode == "scan"
        with pytest.raises(ValueError, match=r"request\.values"):
            author.prepare(request, inputs={"polarity": "negative"})
        invalid = request.copy()
        invalid.values["gain"] = sc.Scan([1, 2])
        with pytest.raises(ValueError, match="gain: input is not scannable"):
            author.prepare(invalid)
        invalid.values["gain"] = 2
        invalid.values["polarity"] = sc.Scan([1, 2])
        with pytest.raises(ValueError, match="polarity"):
            author.prepare(invalid)


def test_imported_request_rejects_changed_declaration_but_can_select_old_revision(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    fixture = reference_lab_daemon
    assert fixture.application.authors is not None
    declaration = fixture.application.authors.get("copied_signal").declaration
    request = declaration(gain=1.0)
    path = fixture.root / "src/reference_lab/workflows/authored/signal.py"
    with AuthorProject(fixture.url) as author:
        author.use(
            parameters=reference_lab_daemon.configuration.ref,
            setup=reference_lab_daemon.configuration.setup,
        )
        original = author.prepare(request)
        try:
            path.write_text(
                fixture.source.replace('= "positive",', '= "negative",'),
                encoding="utf-8",
            )
            changed = author.refresh()
            assert changed.active != original.preview.code_revision
            with pytest.raises(ValueError, match="imported declaration does not match"):
                author.prepare(request)
            retained = author.prepare(
                request, code_revision=original.preview.code_revision
            )
            assert retained.request.inputs == original.request.inputs
            assert retained.preview.code_revision == original.preview.code_revision
        finally:
            path.write_text(fixture.source, encoding="utf-8")
            author.refresh()


def test_required_control_uses_existing_catalog_preview_and_plan_paths(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    with AuthorProject(reference_lab_daemon.url) as author:
        author.use(
            parameters=reference_lab_daemon.configuration.ref,
            setup=reference_lab_daemon.configuration.setup,
        )
        entry = next(
            item for item in author.catalog().entries if item.id == "required_level"
        )
        assert entry.controls[0].default is None
        with pytest.raises(httpx2.HTTPStatusError, match="level"):
            author.prepare("required_level")
        prepared = author.prepare("required_level", scans={"level": [0.1, 0.2]})
        assert prepared.preview.point_count == 2
        saved = prepared.save_plan("Required numeric control", saved_by="operator")
        reopened = author.prepare_plan(saved.ref, actor="operator")
        assert reopened.preview.point_count == 2
        assert reopened.request.control_edits == prepared.request.control_edits


def test_author_inspection_is_bounded_and_retained_without_a_live_client(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    fixture = reference_lab_daemon
    assert fixture.application.authors is not None
    declaration = fixture.application.authors.get("copied_signal").declaration
    request = declaration(gain=1.0)
    request.values["frequency"] = sc.Scan(
        sc.Quantity(float(value), "GHz") for value in np.linspace(4.7, 4.9, 70)
    )
    with AuthorProject(fixture.url) as author:
        author.use(
            parameters=reference_lab_daemon.configuration.ref,
            setup=reference_lab_daemon.configuration.setup,
        )
        prepared = author.prepare(request)
        facts = prepared.inspection
        assert facts.total_point_count == 70
        assert len(facts.points) == facts.sampled_point_limit == 64
        assert facts.points_truncated
        assert facts.selected_point is not None
        assert facts.selected_point.point_index == 0
        assert facts.records[0].id == "result"
        assert any("response" in compute.implementation for compute in facts.computes)
        assert any(
            parameter.kind == "lookup"
            and parameter.table_id == "qubits"
            and parameter.column_id == "drive_carrier_frequency"
            for parameter in facts.parameters
        )
        retained = prepared.preview.model_dump_json()
        request.values["frequency"] = sc.Quantity(4.8, "GHz")
        fixed = author.prepare(request)
        assert fixed.inspection.total_point_count == 1
        assert prepared.preview.model_dump_json() == retained
        # Inspection edits are local copies, not another route to change a launch.
        facts.selected_point.coordinates.clear()
        facts.item_counts.clear()
    assert prepared.inspection.selected_point is not None
    assert prepared.inspection.selected_point.coordinates
    assert prepared.inspection.item_counts
    assert prepared.preview.model_dump_json() == retained


def test_author_reads_ongoing_preview_after_reconnect(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    fixture = reference_lab_daemon
    release = fixture.root / "release-preview"
    try:
        with AuthorProject(fixture.url, receipts=fixture.root / "receipts") as author:
            author.use(
                parameters=reference_lab_daemon.configuration.ref,
                setup=reference_lab_daemon.configuration.setup,
            )
            job = author.prepare(
                "preview_signal",
                scans={"frequency": [*np.linspace(4.7, 4.74, 32), 4.8]},
            ).run()
            receipt = job.receipt
        with AuthorProject(fixture.url) as observer:
            reopened = observer.reopen(receipt)
            deadline = time.monotonic() + 60
            while True:
                preview = reopened.preview(limit=1)
                if preview.latest is not None:
                    break
                assert time.monotonic() < deadline, preview.progress
                time.sleep(0.2)
            assert preview.provisional
            assert 0 <= preview.latest.point_index < 32
            assert preview.live is not None
            assert 1 <= preview.live.received_record_count <= 32
            assert (
                0
                <= preview.live.durable_record_count
                <= preview.live.received_record_count
            )
            assert preview.durable is not None
            assert len(preview.durable.items) <= 1
            assert preview.progress.procedure.closure is None
            step = preview.progress.current_step
            child = preview.progress.current_child
            assert step is not None and child is not None
            assert step.step_key == child.step_key == "experiment"
            assert step.attempt == 1
            assert child.run.snapshot.outcome is None
            assert child.run.control.completed_point_count <= 32
            with pytest.raises(RuntimeError, match="no retained acquisition"):
                reopened.result()
            release.touch()
            reopened.wait(timeout=60)
            assert reopened.result().id == child.run.run_id
            assert (
                len(reopened.result().measurements()["result"].require_values()) == 33
            )
            final = reopened.preview()
            assert final.progress.state == "succeeded"
            assert final.durable is None and final.live is None
    finally:
        release.touch()
