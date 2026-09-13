"""Copy an ordinary author file, edit it, then use Python and real HTTP launch."""

from __future__ import annotations

import shutil
import time
from collections.abc import Generator
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import cast

import httpx2
import numpy as np
import pytest
import scopecat as sc
from scopecat.api.analysis import AnalysisDefinition, AnalysisStep
from scopecat.application import LabApplication
from scopecat.application.author_project import AuthorProject
from scopecat.application.launch import LaunchCatalog, LaunchPreview, LaunchSubmission
from scopecat.daemon.client import DaemonClient, DaemonConflictError
from scopecat.kernel.frozen import thaw_json_value
from scopecat.kernel.quantity import Quantity
from scopecat.project import load_project
from scopecat.records.control_edit import ControlEdit
from scopecat.records.launch_request import LaunchRequest
from scopecat.records.run_request import AxisValuesSourceRecord
from scopecat.records.sample import SampleRevisionDraft
from scopecat_server.author_worker import revision_project
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_testkit.project_loading import isolated_project_imports

from reference_lab.configuration import EXAMPLE_ROOT


@dataclass(frozen=True)
class AuthorDaemon:
    url: str
    application: LabApplication
    source: str
    analysis: AnalysisStep
    root: Path


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
            yield AuthorDaemon(endpoint.base_url, application, source, analysis, root)
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
        before = lab.config.active()
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
                sample=chip.id,
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
                    "config_source": preview.config_source,
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
        assert lab.config.active() == before
        python_run = selected.run(
            lab,
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
        lab.config.set_default(before.config)
        assert provider(lab, command) == admitted
        with pytest.raises(DaemonConflictError, match="active configuration changed"):
            provider(lab, command.model_copy(update={"request_key": "stale-new"}))


def test_author_changes_supported_timing_without_application_edits(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    fixture = reference_lab_daemon
    authors = fixture.application.authors
    assert authors is not None
    with fixture.application.connect(fixture.url) as lab:
        config = lab.config.active().config
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
    from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource

    from reference_lab.parameters import QubitParameters

    fixture = reference_lab_daemon
    with fixture.application.connect(fixture.url) as lab:
        active = lab.config.active()
        sample = lab.samples.create(
            "notebook-context",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Notebook context"),
        )
        saved = lab.config.save_context(
            entry_id="notebook-working-point",
            base=ConfigContextRef(
                entry_id=active.entry.id, content_hash=active.entry.content_hash
            ),
            sample=sample.selector(),
            working_point_id="shifted",
            label="Notebook shifted point",
            parameters=active.config.parameter_snapshot,
        )
        context = ConfigContextRef(
            entry_id=saved.entry.id, content_hash=saved.entry.content_hash
        )
        overrides = (
            sc.parameter_update(
                QubitParameters.drive_carrier_frequency,
                sc.EntityRef(id="q0", kind="logical_qubit"),
                sc.Quantity(5.1, "GHz"),
            ),
        )
        expected = lab.config.resolve_context(context, overrides=overrides)
        with AuthorProject(fixture.url, timeout=120) as authors:
            prepared = authors.prepare(
                "copied_signal", context=context, overrides=overrides
            )
            admitted = prepared.submit(request_key="notebook-context-launch")
        assert prepared.request.context == context
        assert prepared.request.overrides == overrides
        assert prepared.preview.code_revision is not None
        source = prepared.preview.config_source
        assert isinstance(source, ContextRunConfigSource)
        assert source == expected.config_source
        assert source.sample.sample_id == "notebook-context"
        assert source.sample.context_id == "shifted"
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
        assert run.samples[0].context_id == "shifted"
        assert lab.config.active() == active


def test_required_author_input_diagnostics_survive_the_worker_boundary(
    reference_lab_daemon: AuthorDaemon,
) -> None:
    with AuthorProject(reference_lab_daemon.url) as author:
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


def test_editable_request_rebuilds_and_reuses_saved_plan(
    reference_lab_daemon: AuthorDaemon,
    tmp_path: Path,
) -> None:
    fixture = reference_lab_daemon
    assert fixture.application.authors is not None
    declaration = fixture.application.authors.get("copied_signal").declaration
    request = declaration.request(gain=1.0)
    request.values["gain"] = 2.0
    frequencies = np.array([4.7, 4.8, 4.9])
    request.values["frequency"] = sc.Scan(
        sc.Quantity(value, "GHz") for value in cast("list[float]", frequencies.tolist())
    )
    frequencies[:] = 5.2
    with AuthorProject(fixture.url, receipts=tmp_path / "receipts") as author:
        scanned = author.prepare(request)
        assert scanned.request.control_edits["frequency"].mode == "scan"
        assert scanned.preview.point_count == 3
        alternative = request.copy()
        alternative.values["frequency"] = sc.Quantity(4.8, "GHz")
        alternative.values["polarity"] = "negative"
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
    request = declaration.request(gain=1.0)
    path = fixture.root / "src/reference_lab/workflows/authored/signal.py"
    with AuthorProject(fixture.url) as author:
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
