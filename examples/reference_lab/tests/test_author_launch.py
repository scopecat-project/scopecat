"""Copy an ordinary author file, edit it, then use Python and real HTTP launch."""

from __future__ import annotations

import shutil
from collections.abc import Generator
from dataclasses import dataclass
from importlib import import_module
from typing import cast

import httpx2
import numpy as np
import pytest
import scopecat as sc
from scopecat.api.analysis import AnalysisDefinition, AnalysisStep
from scopecat.application import LabApplication
from scopecat.application.controls import ControlEdit
from scopecat.application.launch import (
    LaunchCatalog,
    LaunchPreview,
    LaunchRequest,
    LaunchSubmission,
)
from scopecat.daemon.client import DaemonConflictError
from scopecat.kernel.quantity import Quantity
from scopecat.project import load_project
from scopecat.records.run_request import AxisValuesSourceRecord
from scopecat.records.sample import SampleRevisionDraft
from scopecat_server.lifecycle import start_project, stop_project
from scopecat_testkit.project_loading import isolated_project_imports

from reference_lab.configuration import EXAMPLE_ROOT


@dataclass(frozen=True)
class AuthorDaemon:
    url: str
    application: LabApplication
    source: str
    analysis: AnalysisStep


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
    source_path.write_text(source, encoding="utf-8")
    project = load_project(root / "scopecat.toml")
    with isolated_project_imports():
        load_project(EXAMPLE_ROOT / "scopecat.toml").load_bootstrap()
    with isolated_project_imports():
        application = project.load_application()
        analysis = cast(
            "AnalysisDefinition[...]",
            import_module("reference_lab.workflows.authored.signal").selected_mean,
        )()
        with pytest.MonkeyPatch.context() as patch:
            patch.delenv("SCOPECAT_DAEMON_URL", raising=False)
            endpoint = start_project(project)
        try:
            yield AuthorDaemon(endpoint.base_url, application, source, analysis)
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
                actor="ordinary-author",
                sample=chip.id,
            )
            response = http.post(
                "/api/v1/experiment-launcher/preview",
                json=request.model_dump(mode="json"),
            )
            response.raise_for_status()
            preview = LaunchPreview.model_validate(response.json())
            direct = selected.edit(config=before.config, edits=request.control_edits)
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
        np.testing.assert_array_equal(retained[0], (2.0,))
        assert lab.config.active() == before
        python_run = selected.run(
            lab,
            edits={
                "frequency": sc.axis(
                    selected.controls.fields[0].ref,
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
