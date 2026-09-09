"""Exercise snapshot recovery with real reference-lab data in any installed Python.

Run with the interpreter/environment being checked:
    python packages/scopecat-server/tests/fixtures/snapshot_roundtrip.py TEMPLATE

The template is copied into temporary projects. Each project uses a separate
client process and a real daemon; no editable imports or physical devices are
required. Package wheels must already be installed in the selected interpreter.
"""

# pyright: reportUnknownArgumentType=false

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pyarrow as pa
from pydantic import JsonValue
from scopecat.automation import ProcedureRunListQuery
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.endpoint import DAEMON_URL_ENV, resolve_daemon_endpoint
from scopecat.project import Project, load_project
from scopecat.records.analysis import (
    AnalysisDatasetViewSource,
    AnalysisFigureLayerSpec,
    AnalysisFigureProjection,
    AnalysisUncertaintyProjection,
)

from scopecat_server.lifecycle import DaemonLifecycleError, start_project, stop_project
from scopecat_server.services.project_workers import ProjectProcedureWorkers
from scopecat_server.snapshots import create_snapshot, restore_snapshot, verify_snapshot
from scopecat_server.storage.sqlite.automation import SQLiteAutomationStore
from scopecat_server.storage.sqlite.connection import SQLiteDatabase


def capture(root: Path, *, seed: bool) -> dict[str, JsonValue]:
    project = load_project(root / "scopecat.toml")
    with (
        project.connect() as lab,
        DaemonClient(resolve_daemon_endpoint(root)) as client,
    ):
        if seed:
            # Load the copied project's maintained acceptance producer only after
            # binding the application's imports to this fresh process.
            from reference_lab.acceptance import capture_acceptance_fixtures
            from reference_lab.configuration import bootstrap_config
            from reference_lab.workflows.temperature_diagnostic import (
                TemperatureDiagnosticIntent,
                temperature_diagnostic_procedure,
            )

            fixtures = capture_acceptance_fixtures(lab, client)
            assert fixtures["diagnostic"] is not None
            context = lab.analysis("Snapshot provenance", key="snapshot-provenance")
            first = next(
                item
                for item in client.list_runs().items
                if client.measurement_preview(item.run_id).items
            )
            context.measurements(lab.get_run(first.run_id), id="source")
            published = (
                context.result()
                .fact("verified", True)
                .artifact(
                    "report", text="Retained snapshot analysis", filename="report.txt"
                )
                .dataset("points", pa.table({"x": [0.0, 1.0], "y": [1.0, 2.0]}))
                .figure(dataset="points", kind="scatter", x="x", y="y")
                .save()
            )
            (
                lab.analysis("Snapshot layers", key="snapshot-layers")
                .result()
                .dataset(
                    "fit",
                    pa.table(
                        {
                            "x": [0.0, 1.0],
                            "y": [1.0, 2.0],
                            "lo": [0.9, 1.9],
                            "hi": [1.1, 2.1],
                        }
                    ),
                )
                .figure_layers(
                    layers=(
                        AnalysisFigureLayerSpec(
                            id="measured",
                            source=published.dataset_view_source("points"),
                            projection=AnalysisFigureProjection(
                                kind="scatter", x="x", y="y"
                            ),
                        ),
                        AnalysisFigureLayerSpec(
                            id="fit",
                            source=AnalysisDatasetViewSource(output_id="fit"),
                            projection=AnalysisFigureProjection(
                                kind="line",
                                x="x",
                                y="y",
                                uncertainty=AnalysisUncertaintyProjection(
                                    lower="lo",
                                    upper="hi",
                                    meaning="Declared test bounds",
                                    style="band",
                                ),
                            ),
                        ),
                    )
                )
                .save()
            )
            ready = lab.procedures.submit(
                temperature_diagnostic_procedure,
                TemperatureDiagnosticIntent(initial_config=bootstrap_config()),
                request_key="snapshot-ready",
            )
            assert ready.state == "ready"
        runs = client.list_runs()
        procedures = client.list_procedures(ProcedureRunListQuery())
        assert any(item.state == "ready" for item in procedures.items)
        publication = lab.published_analysis("snapshot-provenance")
        assert publication.fact("verified").value is True
        report = publication.artifact("report").text()
        assert report == "Retained snapshot analysis"
        assert publication.figure("figure").layers[0].preview.series[0].y == [1.0, 2.0]
        layers = lab.published_analysis("snapshot-layers").figure("figure")
        assert layers.layers[1].preview.series[0].y_lower == [0.9, 1.9]
        assert layers.layers[0].source.kind == "published_dataset"
        return {
            "runs": runs.model_dump(mode="json"),
            "measurements": {
                item.run_id: client.measurement_preview(item.run_id).model_dump(
                    mode="json"
                )
                for item in runs.items
            },
            "run_configs": {
                item.run_id: client.run_config(item.run_id).model_dump(mode="json")
                for item in runs.items
            },
            "analyses": {
                item.run_id: client.analyses(item.run_id).model_dump(mode="json")
                for item in runs.items
            },
            "proposals": {
                item.run_id: client.parameter_proposals(item.run_id).model_dump(
                    mode="json"
                )
                for item in runs.items
            },
            "publication": client.project_analysis("snapshot-provenance").model_dump(
                mode="json"
            ),
            "report": report,
            "layered_publication": client.project_analysis(
                "snapshot-layers"
            ).model_dump(mode="json"),
            "registry": client.config_registry().model_dump(mode="json"),
            "activations": client.config_activation_history().model_dump(mode="json"),
            "procedures": procedures.model_dump(mode="json"),
        }


def _capture_process(
    root: Path, output: Path, *, seed: bool = False
) -> dict[str, JsonValue]:
    command = [
        sys.executable,
        str(Path(__file__).resolve()),
        str(root),
        "--output",
        str(output),
    ]
    if seed:
        command.append("--seed")
    # Each capture belongs to its copied project, including under pytest's
    # reference-lab session fixture or a shell with an endpoint override.
    environment = dict(os.environ)
    environment.pop(DAEMON_URL_ENV, None)
    subprocess.run(  # noqa: S603 - fixed interpreter and local script
        command, check=True, env=environment
    )
    return cast("dict[str, JsonValue]", json.loads(output.read_text(encoding="utf-8")))


def _start_fixture_project(project: Project) -> None:
    try:
        start_project(project)
    except DaemonLifecycleError as error:
        log = project.root / ".scopecat" / "daemon.log"
        if log.exists():
            error.add_note(
                "Snapshot fixture daemon log tail:\n"
                + log.read_bytes()[-8192:].decode("utf-8", errors="replace")
            )
        # Do not mask the startup failure if its cleanup also fails.
        try:
            stop_project(project)
        except DaemonLifecycleError as cleanup_error:
            error.add_note(f"Startup cleanup: {cleanup_error}")
        raise


def check_roundtrip(template: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="scopecat-snapshot-check-") as temporary:
        root = Path(temporary)
        source = root / "source"
        source.mkdir()
        for name in ("src", "config"):
            shutil.copytree(template / name, source / name)
        shutil.copy2(template / "scopecat.toml", source / "scopecat.toml")
        project = load_project(source / "scopecat.toml")
        _start_fixture_project(project)
        try:
            before = _capture_process(source, root / "before.json", seed=True)
        finally:
            stop_project(project)

        # Preserve a real durable ready procedure but deliberately include the
        # old GUI's dispatch intent in source state before capture.
        database = SQLiteDatabase(source / ".scopecat/control.sqlite3")
        try:
            store = SQLiteAutomationStore(database)
            ready = [
                item.procedure_run_id
                for item in store.list_runs().items
                if item.state == "ready"
            ]
        finally:
            database.close()
        assert ready
        (source / ".scopecat/console-procedures.json").write_text(
            json.dumps(dict.fromkeys(ready, "active")), encoding="utf-8"
        )
        snapshot = root / "snapshot"
        restored = root / "restored"
        create_snapshot(project, snapshot)
        verify_snapshot(snapshot)
        restore_snapshot(snapshot, restored)
        restored_project = load_project(restored / "scopecat.toml")
        _start_fixture_project(restored_project)
        try:
            after = _capture_process(restored, root / "after.json")
            assert after == before, "restored values or provenance changed"
        finally:
            stop_project(restored_project)

        database = SQLiteDatabase(restored / ".scopecat/control.sqlite3")
        try:
            store = SQLiteAutomationStore(database)
            manager = ProjectProcedureWorkers(
                lambda: restored, lambda key: store.read_run(key).state
            )
            with patch.object(manager, "_spawn") as spawn:
                manager.tick()
                spawn.assert_not_called()
                assert store.read_run(ready[0]).state == "ready"
                manager.dispatch(ready[0])
                spawn.assert_called_once_with(ready[0])
        finally:
            database.close()
        print(
            "snapshot roundtrip verified: measurement, analysis, configuration, "
            "provenance, and explicit dispatch"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed", action="store_true")
    args = parser.parse_args()
    project = cast("Path", args.project)
    output = cast("Path | None", args.output)
    if output is None:
        check_roundtrip(project)
    else:
        output.write_text(
            json.dumps(capture(project, seed=cast("bool", args.seed)), sort_keys=True),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
