"""Manual scientific controls and acquisition share one checked preview boundary."""

import shutil
import time
from pathlib import Path

import httpx2
import pytest
import scopecat as sc
from scopecat.application import LabApplication
from scopecat.project import load_project
from scopecat_instruments import dc_source, rf_source
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.configuration import EXAMPLE_ROOT


def test_manual_query_apply_release_and_launch_keep_relevant_preview_facts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, tmp_path / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", tmp_path / "scopecat.toml")
    project = load_project(tmp_path / "scopecat.toml")
    endpoint = start_project(project)
    target = rf_source("drive-lo-a")
    unrelated = dc_source("bench-source")
    try:
        with (
            LabApplication().connect(endpoint.base_url) as lab,
            project.authoring() as authors,
        ):
            with lab.instruments.open(target) as devices:
                source = devices[target]
                observation = source.frequency.read_observation()
                assert observation.source == "hardware_query"
                assert observation.observed_at is not None
                defaults = source.apply_defaults()
                assert defaults.status == "rejected"
                assert (
                    defaults.problems[0].code
                    == "instrument_configured_defaults_missing"
                )
                assert (
                    source.apply(frequency=sc.Quantity(4.9, "GHz")).status == "applied"
                )
            assert lab.instruments.release(target).instrument_ids == ("drive-lo-a",)
            prepared = authors.prepare("ramsey")
            assert "drive-lo-a" in prepared.preview.resources
            fence = prepared.preview.manual_state
            assert fence is not None
            assert fence.binding.code_revision == prepared.preview.code_revision
            assert authors.manual_preview_validity(fence).valid
            with lab.instruments.open(target) as devices:
                observed = devices[target].frequency.read_observation()
                assert observed.source == "hardware_query"
            assert authors.manual_preview_validity(fence).valid
            with lab.instruments.open(unrelated) as devices:
                assert (
                    devices[unrelated].apply(output_enabled=False).status == "applied"
                )
            assert authors.manual_preview_validity(fence).valid
            with lab.instruments.open(target) as devices:
                assert (
                    devices[target].apply(frequency=sc.Quantity(4.95, "GHz")).status
                    == "applied"
                )
            invalid = authors.manual_preview_validity(fence)
            assert not invalid.valid
            assert invalid.changes[0].instrument_ids == ("drive-lo-a",)
            with pytest.raises(httpx2.HTTPStatusError, match="422") as rejected:
                prepared.submit(request_key="stale-preview")
            assert "Manual operation changed drive-lo-a" in rejected.value.response.text
            before_abort = authors.prepare("ramsey")
            assert before_abort.preview.manual_state is not None
            with lab.instruments.open(target) as devices:
                devices.abort()
            assert not authors.manual_preview_validity(
                before_abort.preview.manual_state
            ).valid
            fresh = authors.prepare("ramsey")
            submission = fresh.submit(request_key="fresh-preview")
            procedure = lab.procedures.get(submission.procedure_id)
            deadline = time.monotonic() + 30
            while (
                procedure.state in {"ready", "leased"} and time.monotonic() < deadline
            ):
                time.sleep(0.05)
            assert procedure.state == "closed"
            output = procedure.output("experiment")
            assert output.kind == "run"
            assert lab.get_run(output.run_id).status == "completed"
            lab.instruments.release(target)
            # A changed manual state blocks new work, while a lost-receipt retry
            # returns the same admitted procedure and does not acquire again.
            assert (
                fresh.submit(request_key="fresh-preview").procedure_id
                == submission.procedure_id
            )
            with pytest.raises(httpx2.HTTPStatusError, match="422"):
                fresh.submit(request_key="different-key")
    finally:
        stop_project(project)
