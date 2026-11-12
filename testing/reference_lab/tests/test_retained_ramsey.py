"""Real worker acquisition, point-local statistics and durable retention intent."""

from __future__ import annotations

from typing import Literal

import numpy as np

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.workflows.retained_ramsey import retained_ramsey
from scopecat.records.measurement import MeasurementArray, MeasurementScalar
from scopecat.records.parameter_revision import ParameterRevision


def test_ramsey_retention_round_trips_across_clients(
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
) -> None:
    run_ids: dict[str, str] = {}
    modes: tuple[Literal["raw", "stats", "both"], ...] = ("raw", "stats", "both")
    with create_application(EXAMPLE_ROOT).connect(independent_lab_daemon) as lab:
        inputs = lab.parameters.resolve(
            independent_parameters, setup=lab.setup.get("initial")
        )
        for mode in modes:
            prepared = lab.prepare(retained_ramsey.build(retain=mode), config=inputs)
            preview = prepared.preview()
            assert len(preview.computes) == (0 if mode == "raw" else 1)
            if mode != "raw":
                assert preview.computes[0].placement == "observation"
            run = prepared.run()
            assert run.status == "completed"
            run_ids[mode] = run.id

    # Use only the durable result contract, not a reconstructed authored output.
    with create_application(EXAMPLE_ROOT).connect(independent_lab_daemon) as lab:
        datasets = {
            mode: lab.get_run(run_id).measurements() for mode, run_id in run_ids.items()
        }
        for mode, data in datasets.items():
            expected: set[str] = ({"raw"} if mode != "stats" else set[str]()) | (
                {"stats/count", "stats/mean"} if mode != "raw" else set[str]()
            )
            assert len(data.records) == 4
            result = lab.get_run(run_ids[mode]).result()
            assert {"/".join(path) for path in result.paths} == expected | {
                "delay",
                "phase",
            }
            for record in data.records:
                assert set(record.observables) == expected
            if mode != "raw":
                assert data["stats/mean"].definition.dtype == "complex128"
                assert data["stats/mean"].definition.unit == "ratio"
                assert data["stats/mean"].definition.dims == ("point",)
                assert (
                    data["stats/mean"].definition.metadata["reduction_axis"] == "shot"
                )
                assert data["stats/count"].definition.dtype == "int64"

        for raw_record, stats_record, both_record in zip(
            datasets["raw"].records,
            datasets["stats"].records,
            datasets["both"].records,
            strict=True,
        ):
            raw = raw_record.observables["raw"]
            both = both_record.observables["raw"]
            assert isinstance(raw, MeasurementArray) and isinstance(
                both, MeasurementArray
            )
            assert raw.shape == both.shape == (16,)
            np.testing.assert_array_equal(raw.values, both.values)
            for record in (stats_record, both_record):
                count = record.observables["stats/count"]
                mean = record.observables["stats/mean"]
                assert isinstance(count, MeasurementScalar) and count.value == 16
                assert isinstance(mean, MeasurementScalar) and isinstance(
                    mean.value, complex
                )
                np.testing.assert_allclose(
                    mean.value,
                    np.mean(np.asarray(raw.values, dtype=np.complex128)),
                    rtol=1e-14,
                    atol=1e-14,
                )
