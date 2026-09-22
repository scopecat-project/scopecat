from __future__ import annotations

from pathlib import Path
from runpy import run_path
from typing import Protocol, cast

from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import MeasurementTracePreviewQuery


class _ReferenceLabDaemon(Protocol):
    url: str


def test_multichannel_dc_bias_spans_two_devices_and_four_routes(
    reference_lab_daemon: _ReferenceLabDaemon,
    reference_lab_notebooks: Path,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(reference_lab_notebooks / "33_multichannel_dc_bias.py"))
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
    reference_lab_notebooks: Path,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(reference_lab_notebooks / "34_xy_lo_sweep.py"))
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
    reference_lab_notebooks: Path,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(reference_lab_notebooks / "35_awg_output_monitor.py"))
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


def test_flux_ramsey_composes_local_bias_and_quantum_channels(
    reference_lab_daemon: _ReferenceLabDaemon,
    reference_lab_notebooks: Path,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(reference_lab_notebooks / "24_flux_ramsey.py"))
    summary = cast("dict[str, object]", namespace["flux_ramsey_summary"])

    assert summary["points"] == 15
    assert summary["records"] == 15
    assert summary["status"] == "completed"
    assert sorted(cast("dict[str, int]", summary["dimensions"]).values()) == [3, 5]


def test_entity_routed_ramsey_switches_channel_sets_by_point(
    reference_lab_daemon: _ReferenceLabDaemon,
    reference_lab_notebooks: Path,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(reference_lab_notebooks / "25_entity_routed_ramsey.py"))
    summary = cast("dict[str, object]", namespace["entity_ramsey_summary"])

    assert summary == {
        "points": 6,
        "records": 6,
        "qubit_groups": 2,
        "status": "completed",
    }


def test_channel_conflict_names_the_logical_drive_route(
    reference_lab_daemon: _ReferenceLabDaemon,
    reference_lab_notebooks: Path,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(
        str(reference_lab_notebooks / "28_channel_conflict_diagnostic.py")
    )
    summary = cast("dict[str, object]", namespace["channel_conflict_summary"])

    assert "pulse_signal_overlap" in cast("list[str]", summary["codes"])
    assert summary["mentions_drive_q0"] is True


def test_entity_axis_preserves_the_available_demod_channel(
    reference_lab_daemon: _ReferenceLabDaemon,
    reference_lab_notebooks: Path,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(reference_lab_notebooks / "29_channel_unavailable.py"))
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
    reference_lab_notebooks: Path,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(reference_lab_notebooks / "31_topology_scaled_ramsey.py"))
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


def test_ragged_scope_data_survives_daemon_boundaries(
    reference_lab_daemon: _ReferenceLabDaemon,
    reference_lab_notebooks: Path,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    namespace = run_path(str(reference_lab_notebooks / "50_ragged_scope_capture.py"))
    summary = cast("dict[str, object]", namespace["ragged_scope_summary"])

    assert summary == {
        "record_lengths": [4, 7, 10],
        "ragged_shapes": [[4], [7], [10]],
        "window_shapes": [[2], [2], [2]],
        "status": "completed",
    }
