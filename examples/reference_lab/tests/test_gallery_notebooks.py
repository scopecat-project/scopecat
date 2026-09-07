from __future__ import annotations

from pathlib import Path
from runpy import run_path
from typing import Protocol, cast

import pytest
from pydantic import ValidationError
from scopecat.api.published_analysis import PublishedAnalysis
from scopecat.api.run import RunHandle
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import MeasurementTracePreviewQuery
from scopecat.kernel.errors import SessionClosedError
from scopecat.measurements.dataset import Dataset
from scopecat.records.run import ConfigRegistryRunConfigSource, RunSnapshot


class _ReferenceLabDaemon(Protocol):
    url: str


NOTEBOOKS = Path(__file__).parents[1] / "notebooks"


def test_lab_tour_shows_one_inventory_and_parameter_catalog(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "00_lab_tour.py"))
    summary = cast("dict[str, object]", namespace["lab_tour_summary"])

    assert set(cast("list[str]", summary["instruments"])) == {
        "pump-source",
        "bench-source",
        "drive-lo-a",
        "drive-lo-b",
        "readout-lo",
        "drive-awg",
        "readout-awg",
        "readout-digitizer",
        "timing-controller",
        "bench-scope",
        "flux-dac-a",
        "flux-dac-b",
        "mixing-chamber",
        "readout-vna",
    }
    assert summary["parameter_rows"] == {
        "qubits": 4,
        "iq_chains": 5,
        "awg_output_baselines": 1,
        "lo_groups": 3,
        "readout_resonators": 4,
        "channel_calibrations": 4,
        "bias_profiles": 8,
    }


def test_sample_workflow_binds_exact_revision_and_analysis_subject(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "05_sample_workflow.py"))
    summary = cast("dict[str, object]", namespace["sample_workflow_summary"])

    assert summary == {
        "sample_id": "chip-a17",
        "active_revision": 2,
        "bound_revision": 2,
        "binding_role": "subject",
        "binding_context": "virtual-cooldown-1",
        "run_status": "completed",
        "analysis_subject": "sample",
        "analysis_inputs": 1,
    }


def test_scan_shapes_run_as_real_lab_experiments(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "21_scan_shapes.py"))
    summary = cast("dict[str, object]", namespace["scan_shapes_summary"])

    assert summary == {
        "point_cloud_points": 4,
        "point_cloud_layout": "point_cloud",
        "point_cloud_rows": 4,
        "repeated_grid_points": 8,
        "repeated_grid_layout": "product_grid",
        "repeated_grid_rows": 8,
    }


def test_channel_map_exposes_independent_drive_and_demod_routes(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "22_channel_map.py"))
    summary = cast("dict[str, object]", namespace["channel_map_summary"])

    assert summary == {
        "drive": {
            "q0": {"i": "drive.awg0.ch1", "q": "drive.awg0.ch2"},
            "q1": {"i": "drive.awg0.ch3", "q": "drive.awg0.ch4"},
            "q2": {"i": "drive.awg0.ch5", "q": "drive.awg0.ch6"},
            "q3": {"i": "drive.awg0.ch7", "q": "drive.awg0.ch8"},
        },
        "readout": {
            "q0": {"i": "readout.awg0.ch1", "q": "readout.awg0.ch2"},
            "q1": {"i": "readout.awg0.ch1", "q": "readout.awg0.ch2"},
            "q2": {"i": "readout.awg0.ch1", "q": "readout.awg0.ch2"},
            "q3": {"i": "readout.awg0.ch1", "q": "readout.awg0.ch2"},
        },
        "acquisition": {
            "q0": {
                "adc": ("readout-digitizer", ("inputs", "ch1")),
                "demodulator": "demod0",
            },
            "q1": {
                "adc": ("readout-digitizer", ("inputs", "ch1")),
                "demodulator": "demod1",
            },
            "q2": {
                "adc": ("readout-digitizer", ("inputs", "ch1")),
                "demodulator": "demod2",
            },
            "q3": {
                "adc": ("readout-digitizer", ("inputs", "ch1")),
                "demodulator": "demod3",
            },
        },
        "flux": {
            "q0": ("flux-dac-a", "flux.dac_a.ch1"),
            "q1": ("flux-dac-a", "flux.dac_a.ch2"),
            "q2": ("flux-dac-b", "flux.dac_b.ch1"),
            "q3": ("flux-dac-b", "flux.dac_b.ch2"),
        },
    }


def test_multichannel_dc_bias_spans_two_devices_and_four_routes(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "33_multichannel_dc_bias.py"))
    summary = cast("dict[str, object]", namespace["multichannel_dc_bias_summary"])

    assert summary == {
        "devices": ["flux-dac-a", "flux-dac-b"],
        "routes": {
            "q0": ("flux-dac-a", "flux.dac_a.ch1"),
            "q1": ("flux-dac-a", "flux.dac_a.ch2"),
            "q2": ("flux-dac-b", "flux.dac_b.ch1"),
            "q3": ("flux-dac-b", "flux.dac_b.ch2"),
        },
        "profile": "operate",
        "physical_bias_mv": {
            "q0": -78.4,
            "q1": 22.4,
            "q2": 39.4,
            "q3": -96.0,
        },
        "readback_mv": {
            "q0": -78.4,
            "q1": 22.4,
            "q2": 39.4,
            "q3": -96.0,
        },
        "settled": {"q0": True, "q1": True, "q2": True, "q3": True},
        "records": 1,
        "status": "completed",
    }


def test_xy_lo_sweep_records_carriers_from_signed_if(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "34_xy_lo_sweep.py"))
    summary = cast("dict[str, object]", namespace["xy_lo_sweep_summary"])

    assert summary == {
        "requested_lo_ghz": [4.9, 4.91, 4.92],
        "requested_signed_if_mhz": {"q0": 100.0, "q1": -100.0},
        "requested_carrier_ghz": {
            "q0": [5.0, 5.01, 5.02],
            "q1": [4.8, 4.81, 4.82],
        },
        "status": "completed",
    }


def test_awg_output_monitor_records_entityless_bench_capture(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "35_awg_output_monitor.py"))
    summary = cast("dict[str, object]", namespace["awg_output_monitor_summary"])

    assert summary == {
        "name": "AWG CH1 pulse shape after bench recabling",
        "tags": ["diagnostic", "awg-monitor"],
        "description_mentions_wiring": True,
        "samples": 16,
        "time_end_ns": 15.0,
        "peak_mv": 250.0,
        "minimum_mv": -20.0,
        "status": "completed",
    }


def test_q0_ramsey_runs_on_the_reference_channels(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "23_q0_ramsey.py"))
    summary = cast("dict[str, object]", namespace["q0_ramsey_summary"])

    assert summary == {
        "points": 5,
        "records": 5,
        "probability_samples": 5,
        "status": "completed",
    }


def test_flux_ramsey_composes_local_bias_and_quantum_channels(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "24_flux_ramsey.py"))
    summary = cast("dict[str, object]", namespace["flux_ramsey_summary"])

    assert summary["points"] == 15
    assert summary["records"] == 15
    assert summary["status"] == "completed"
    assert sorted(cast("dict[str, int]", summary["dimensions"]).values()) == [3, 5]


def test_entity_routed_ramsey_switches_channel_sets_by_point(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "25_entity_routed_ramsey.py"))
    summary = cast("dict[str, object]", namespace["entity_ramsey_summary"])

    assert summary == {
        "points": 6,
        "records": 6,
        "qubit_groups": 2,
        "status": "completed",
    }


def test_parallel_ramsey_uses_two_drive_and_demod_channels(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "26_parallel_multiplexed_ramsey.py"))
    summary = cast("dict[str, object]", namespace["parallel_ramsey_summary"])

    assert summary == {
        "points": 3,
        "records": 3,
        "q0_samples": 3,
        "q1_samples": 3,
        "status": "completed",
    }


def test_fixed_if_lo_sweep_keeps_lo_outside_the_quantum_target(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "36_q0_fixed_if_lo_sweep.py"))
    summary = cast("dict[str, object]", namespace["q0_fixed_if_lo_sweep_summary"])

    assert summary == {
        "points": 3,
        "signed_if_mhz": [-50.0],
        "carrier_ghz": [4.79, 4.8, 4.81],
        "status": "completed",
    }


def test_channel_timing_candidate_preserves_analysis_provenance(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "27_channel_timing_candidate.py"))
    summary = cast("dict[str, object]", namespace["channel_candidate_summary"])

    assert summary["proposal_id"] == "q1-channel-delay"
    assert summary["candidate_status"] == "completed"
    assert summary["candidate_provenance"] is True


def test_channel_conflict_names_the_logical_drive_route(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "28_channel_conflict_diagnostic.py"))
    summary = cast("dict[str, object]", namespace["channel_conflict_summary"])

    assert "pulse_signal_overlap" in cast("list[str]", summary["codes"])
    assert summary["mentions_drive_q0"] is True


def test_entity_axis_preserves_the_available_demod_channel(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "29_channel_unavailable.py"))
    summary = cast("dict[str, object]", namespace["channel_unavailable_summary"])

    assert isinstance(summary["run_id"], str)
    assert summary["status"] == "completed"
    assert summary["records"] == 2
    assert summary["variable"] == "iq_shots"
    assert summary["dims"] == [
        "point",
        "logical_qubit",
        "shared/parallel-two-qubit-ramsey/shot",
    ]
    assert summary["shape"] == [2, 2, 64]
    assert summary["entities"] == ["q0", "q1"]
    assert summary["available_points"] == {"q0": 2, "q1": 1}
    assert summary["unavailable_reasons"] == {"q0": [], "q1": ["missing"]}
    source_results = cast("dict[str, str]", summary["source_results"])
    assert source_results.keys() == {"q0", "q1"}
    assert source_results["q0"].endswith("q0_iq_shots")
    assert source_results["q1"].endswith("q1_iq_shots")
    assert summary["acquisition_policy"] == "independent"
    with DaemonClient(reference_lab_daemon.url) as client:
        trace = client.measurement_trace_preview(
            summary["run_id"],
            MeasurementTracePreviewQuery(
                observable_id="iq_shots",
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


def test_topology_scaled_ramsey_resolves_one_connected_qubit_set(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "31_topology_scaled_ramsey.py"))
    summary = cast("dict[str, object]", namespace["topology_scaled_summary"])

    assert summary == {
        "points": 3,
        "records": 3,
        "variable": "iq_shots",
        "dims": [
            "point",
            "shared/topology-scaled-ramsey/targets",
            "shared/topology-scaled-ramsey/shot",
        ],
        "shape": [3, 3, 64],
        "entities": ["q1", "q0", "q2"],
        "tree_has_parallel_each": True,
        "status": "completed",
    }


def test_quantum_program_exposes_compiled_inspection_layers(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "32_quantum_program_inspection.py"))
    summary = cast("dict[str, object]", namespace["program_inspection_summary"])

    assert summary["program_id"] == "reference-lab.topology-scaled-ramsey"
    assert summary["description_has_ports"] is True
    assert summary["tree_has_parallel_each"] is True
    assert summary["layers"] == ["authored", "logical", "scheduled", "physical"]
    assert cast("int", summary["physical_matching_nodes"]) > 8
    assert summary["physical_returned_nodes"] == 8
    assert summary["snapshot_matches_artifact"] is True


def test_drag_calibration_closes_the_reviewed_config_loop(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "30_drag_calibration.py"))
    summary = cast("dict[str, object]", namespace["drag_beta_summary"])

    assert cast("str", summary["procedure"]).startswith("procedure-")
    assert summary["procedure_state"] == "closed"
    assert summary["procedure_status"] == "succeeded"
    assert summary["procedure_steps"] == {
        "baseline": "succeeded",
        "fit": "succeeded",
        "candidate": "succeeded",
        "verification": "succeeded",
        "accept": "succeeded",
    }
    assert summary["status"] == "completed"
    assert summary["point_count"] == 15
    assert summary["output_kinds"] == [
        "dataset",
        "fact",
        "table",
        "dataset",
        "figure",
        "artifact",
        "parameter_change_proposal",
    ]
    figure = cast("PublishedAnalysis", namespace["analysis"]).figure(
        "observations-by-amplification"
    )
    assert [layer.id for layer in figure.layers] == ["measured", "fit"]
    assert [layer.preview.kind for layer in figure.layers] == ["scatter", "line"]
    assert figure.layers[0].total_points == 15
    assert figure.layers[1].total_points == 243
    assert figure.layers[1].projection.uncertainty is not None
    assert (
        "not a confidence interval" in figure.layers[1].projection.uncertainty.meaning
    )
    assert all(series.y_lower is not None for series in figure.layers[1].preview.series)
    assert summary["execution_evidence"] == 0
    assert summary["fit_report"] == "drag-beta-fit.md"
    assert summary["proposal_evidence"] == ("quadratic-fit", "observations")
    assert summary["verification_subject"] == "project"
    verification_inputs = cast(
        "list[tuple[str, str, str]]", summary["verification_inputs"]
    )
    assert [item[0] for item in verification_inputs] == ["baseline", "candidate"]
    assert [item[2] for item in verification_inputs] == ["baseline", "candidate"]
    assert cast("float", summary["verification_improvement"]) >= 0.001
    assert summary["verification_accepted"]
    assert summary["verification_report"] == "drag-beta-verification.md"
    assert summary["verification_is_project_owned"]
    assert summary["accepted_verification"] == summary["verification"]
    assert summary["accepted_output_matches_entry"]
    assert summary["candidate_run_uses_analysis"]
    assert summary["production_source_identity"] == summary["accepted_source_identity"]
    accepted_source_identity = cast(
        "tuple[str, str, str, str, int]", summary["accepted_source_identity"]
    )
    assert accepted_source_identity[0] == "active"
    assert summary["production_config_content_hash"] == accepted_source_identity[3]
    assert summary["accepted_as_default"]
    assert cast("str", summary["restore_operation"]).startswith(
        "reference-lab.drag-beta.restore:procedure-"
    )
    assert summary["default_restored"]


def test_measurement_workbench_uses_real_durable_data(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "40_measurement_workbench.py"))
    summary = cast("dict[str, object]", namespace["measurement_summary"])

    assert summary["points"] == 3
    assert summary["nearest_points"] == 1
    assert summary["first_two_points"] == 2
    assert summary["available_points"] == 3
    assert summary["groups"] == 3
    assert summary["arrow_rows"] == 3
    assert summary["batch_sizes"] == [2, 1]


def test_ragged_scope_data_survives_daemon_boundaries(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(NOTEBOOKS / "50_ragged_scope_capture.py"))
    summary = cast("dict[str, object]", namespace["ragged_scope_summary"])

    assert summary == {
        "record_lengths": [4, 7, 10],
        "ragged_shapes": [[4], [7], [10]],
        "window_shapes": [[2], [2], [2]],
        "status": "completed",
    }


def test_session_lifetime_captures_and_reattaches_without_reacquisition(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    with DaemonClient(reference_lab_daemon.url) as client:
        before = {run.run_id for run in client.list_runs(limit=100).items}
        namespace = run_path(str(NOTEBOOKS / "02_session_lifetime.py"))
        after = {run.run_id for run in client.list_runs(limit=100).items}
    snapshot = cast("RunSnapshot", namespace["snapshot"])
    assert after - before == {snapshot.run_id}
    assert namespace["session_lifetime_summary"] == {
        "run_id": snapshot.run_id,
        "status": "completed",
        "same_snapshot": True,
        "same_measurements": True,
        "records": 1,
        "sessions_closed": True,
    }
    run = cast("RunHandle", namespace["run"])
    with pytest.raises(SessionClosedError, match=r"lab.get_run"):
        _ = run.status
    lazy = cast("Dataset", namespace["lazy_measurements"])
    with pytest.raises(SessionClosedError, match="session is closed"):
        _ = lazy.records
    with pytest.raises(ValidationError, match="frozen_instance"):
        snapshot.run_id = "changed"
    assert isinstance(snapshot.config_source, ConfigRegistryRunConfigSource)
    with pytest.raises(ValidationError, match="frozen_instance"):
        snapshot.config_source.entry_id = "changed"
