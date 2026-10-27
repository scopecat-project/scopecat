"""A real baseline author/compiler/interpreter control, not the port benchmark."""

from experiments.result_ledger.baseline_flow import run_baseline_flow


def test_baseline_compiles_and_executes_two_dimensional_waveforms(tmp_path):
    report = run_baseline_flow(tmp_path / "baseline")
    assert report["status"] == "completed"
    assert report["points"] == 4
    assert report["waveform_shape_per_point"] == [2, 8]
    assert len(report["scientific_values_sha256"]) == 64
