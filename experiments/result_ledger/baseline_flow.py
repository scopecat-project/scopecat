# ruff: noqa: TID251
"""Exercise the baseline author/compiler/interpreter, not storage-port fixtures.

This is an actual production 2D authoring invocation and array-valued host
compute. Testkit supplies isolated SQLite session/admission ports; no device
provider runs. It verifies complete two-channel waveforms and coordinate units.
It does not pretend to cover hardware synchronization, partial availability,
adaptive recovery, or the replacement's cross-point result bundles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Annotated

import numpy as np
from numpy.typing import NDArray

import scopecat as sc
import scopecat.execution.interpreter as baseline_interpreter
import scopecat_server.storage.sqlite.execution as baseline_storage
import scopecat_testkit.server.execution as baseline_testkit
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.system import ExperimentSystem
from scopecat.records.config import config_content_hash
from scopecat.runs.service import read_run_measurement_dataset
from scopecat_testkit.server.execution import execute_invocation_run
from scopecat_testkit.server.runtime import sqlite_project_services
from scopecat_testkit.workflow_fixtures import load_config

from .benchmark import BASELINE_SHA, _git

SAMPLES = 8


@sc.compute
def synthetic_waveform(
    bias: Annotated[float, sc.Unit("V")],
    frequency: Annotated[float, sc.Unit("Hz")],
) -> Annotated[
    NDArray[np.float64],
    sc.ArrayType(
        dtype="float64",
        unit="V",
        dimensions=(
            sc.ArrayDimension("channel", 2),
            sc.ArrayDimension("sample", SAMPLES, kind="time", unit="s"),
        ),
    ),
]:
    """Host compute is the explicit synthetic acquisition substitute."""
    point = int(bias) * 2 + int(frequency) - 1
    return np.asarray(
        [
            [point + channel / 10 + sample / 1000 for sample in range(SAMPLES)]
            for channel in range(2)
        ],
        dtype=np.float64,
    )


@sc.experiment(id="experiment1344.baseline_2d_waveform")
def baseline_waveform(experiment: sc.ExperimentContext) -> dict[str, object]:
    bias = experiment.scan("bias", (sc.Quantity(0.0, "V"), sc.Quantity(1.0, "V")))
    frequency = experiment.scan(
        "frequency", (sc.Quantity(1.0, "Hz"), sc.Quantity(2.0, "Hz"))
    )
    return {
        "bias": bias,
        "frequency": frequency,
        "waveform": synthetic_waveform(bias=bias, frequency=frequency),
    }


def run_baseline_flow(directory: Path, baseline_repo: Path | None = None) -> dict:
    """Require a new directory, execute production planning, read persisted data."""
    imported_repo = Path(sc.__file__).resolve().parents[4]
    baseline_repo = imported_repo if baseline_repo is None else baseline_repo.resolve()
    sources = {
        "core": Path(baseline_interpreter.__file__).resolve(),
        "server": Path(baseline_storage.__file__).resolve(),
        "testkit": Path(baseline_testkit.__file__).resolve(),
    }
    expected_roots = {
        "core": baseline_repo / "packages/scopecat/src",
        "server": baseline_repo / "packages/scopecat-server/src",
        "testkit": baseline_repo / "testing/scopecat-testkit/src",
    }
    if imported_repo != baseline_repo or any(
        not source.is_relative_to(expected_roots[name])
        for name, source in sources.items()
    ):
        raise RuntimeError(
            "baseline imports do not match --baseline-repo; use that checkout's "
            "interpreter and unset conflicting PYTHONPATH. This option validates "
            "sources; it does not reroute imports."
        )
    if _git(baseline_repo, "rev-parse", "HEAD") != BASELINE_SHA:
        raise RuntimeError("baseline source imports must match the fixed main SHA")
    if _git(baseline_repo, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("baseline tracked edits would invalidate this control")
    directory.mkdir(parents=True, exist_ok=False)
    config = load_config()
    manifest = execute_invocation_run(
        config=config,
        experiment=baseline_waveform.build(),
        system=ExperimentSystem(
            instrument_catalog=InstrumentContractCatalog(
                config_content_hash=config_content_hash(config)
            )
        ),
        instrument_backend=None,
        project_root=directory,
    )
    dataset = read_run_measurement_dataset(
        run_id=manifest.run_id, services=sqlite_project_services(directory)
    ).dataset
    observed = []
    for record in dataset.records:
        array = record.observables["waveform"]
        assert array.shape == (2, SAMPLES)
        assert array.dtype == "float64" and array.unit == "V"
        assert array.availability is None
        assert record.coordinates["bias"].unit == "V"
        assert record.coordinates["frequency"].unit == "Hz"
        observed.append(
            [
                record.point_index,
                record.coordinates["bias"].value,
                record.coordinates["frequency"].value,
                array.values.tolist(),
            ]
        )
    expected = [
        [
            point,
            float(point // 2),
            float(point % 2 + 1),
            [
                [point + channel / 10 + sample / 1000 for sample in range(SAMPLES)]
                for channel in range(2)
            ],
        ]
        for point in range(4)
    ]
    assert observed == expected
    assert manifest.status == "completed"
    report = {
        "baseline_sha": BASELINE_SHA,
        "baseline_source": str(baseline_repo),
        "module_paths": {name: str(source) for name, source in sources.items()},
        "run_id": manifest.run_id,
        "status": manifest.status,
        "points": len(observed),
        "waveform_shape_per_point": [2, SAMPLES],
        "scientific_values_sha256": hashlib.sha256(
            json.dumps(observed, separators=(",", ":")).encode()
        ).hexdigest(),
        "chain": "authoring -> compiler/planning -> interpreter -> SQLite -> dataset",
        "substitute": "typed host compute returns synthetic waveform; no hardware",
        "not_covered": [
            "partial availability",
            "hardware synchronization or physical attempt identity",
            "cross-point bundles",
            "fault injection/adaptive recovery/GUI",
        ],
    }
    (directory / "baseline-flow.json").write_text(json.dumps(report, indent=2))
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument(
        "--baseline-repo",
        type=Path,
        help="Validate import checkout; does not change Python import routing",
    )
    args = parser.parse_args(argv)
    print(json.dumps(run_baseline_flow(args.directory, args.baseline_repo), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
