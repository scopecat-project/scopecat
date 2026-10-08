"""Capture production reference-lab responses in a temporary, hardware-free project."""

from __future__ import annotations

import argparse
import shutil
from difflib import unified_diff
from itertools import islice
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from reference_lab.acceptance import (
    acceptance_json,
    acceptance_json_matches,
    capture_acceptance_fixtures,
)
from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from scopecat.api.lab import LabClient
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import MeasurementTracePreviewQuery
from scopecat.project import load_project
from scopecat.records.measurement import MeasurementUnavailable
from scopecat_server.lifecycle import start_project, stop_project

OUTPUT = EXAMPLE_ROOT / "fixtures" / "acceptance.json"


def _check_independent_readout(lab: LabClient, client: DaemonClient) -> None:
    """Check the captured worker result without another gallery acquisition."""
    [candidate_source] = [
        run.snapshot.config_source
        for run in client.list_runs().items
        if run.snapshot.config_source is not None
        and run.snapshot.config_source.kind == "analysis_candidate"
    ]
    assert candidate_source.kind == "analysis_candidate"
    source = lab.get_run(candidate_source.source_run_id)
    # Reuse this real worker acquisition for the retired unavailable-channel
    # gallery contract. No second run or presentation summary is needed.
    assert source.status == "completed"
    data = source.measurements()
    schema = data.schema
    iq_ref = "iq_shots"
    iq = data[iq_ref]
    [entity_axis] = [
        dimension
        for dimension in schema.dimensions
        if dimension.kind == "entity" and dimension.id in iq.dims
    ]
    assert entity_axis.index is not None
    entities = entity_axis.index.values
    assert [entity.id for entity in entities] == ["q0", "q1"]
    assert len(data) == 2 and iq.shape == (2, 2, 64)
    products = iq.definition.source_entity_products
    assert products is not None
    assert len(products.product_ids) == len(entities)
    for entity, product_id in zip(entities, products.product_ids, strict=True):
        assert product_id is not None
        assert product_id.endswith(f"{entity.id}_iq_shots")
        selected = data.sel({entity_axis.id: entity})[iq_ref]
        failures = [
            value.reason if isinstance(value, MeasurementUnavailable) else None
            for value in selected.raw_values
        ]
        assert failures == ([None, None] if entity.id == "q0" else [None, "missing"])
    acquisition = iq.definition.entity_acquisition
    assert acquisition is not None and acquisition.policy == "independent"
    trace = client.measurement_trace_preview(
        source.id,
        MeasurementTracePreviewQuery(
            observable_id=iq.id,
            entity_indices=(0, 1),
            max_series=4,
            max_samples=256,
        ),
    )
    assert [series.label for series in trace.series] == [
        "Delay 88 ns · q0",
        "Delay 88 ns · q1",
        "Delay 128 ns · q0",
    ]
    assert [failure.label for failure in trace.failures] == ["Delay 128 ns · q1"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="scopecat-acceptance-") as temporary:
        root = Path(temporary)
        shutil.copytree(EXAMPLE_ROOT / "config", root / "config")
        shutil.copytree(EXAMPLE_ROOT / "src", root / "src")
        shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
        project = load_project(root / "scopecat.toml")
        endpoint = start_project(project)
        try:
            with (
                create_application(root).connect(endpoint.base_url) as lab,
                DaemonClient(endpoint.base_url) as client,
            ):
                assert lab.config.registry().entries == ()
                content = acceptance_json(capture_acceptance_fixtures(lab, client))
                _check_independent_readout(lab, client)
                assert lab.config.registry().entries == ()
        finally:
            stop_project(project)
    if cast("bool", args.check):
        expected = OUTPUT.read_text() if OUTPUT.is_file() else ""
        if not expected or not acceptance_json_matches(expected, content):
            differences = unified_diff(
                expected.splitlines(),
                content.splitlines(),
                fromfile="committed acceptance.json",
                tofile="fresh acceptance.json",
                n=2,
                lineterm="",
            )
            # Keep CI diagnostics bounded without rewriting captured scientific data.
            for line in islice(differences, 80):
                print(line[:300])
            raise SystemExit(
                "Reference-lab acceptance fixture is stale; run "
                "uv run python scripts/generate_reference_lab_acceptance.py"
            )
    else:
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_text(content)


if __name__ == "__main__":
    main()
