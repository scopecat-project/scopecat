from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from runpy import run_path
from typing import Protocol, TypedDict, assert_type, cast

import numpy as np
import pytest
import scopecat as sc
from scopecat.kernel.errors import RunIndeterminate
from scopecat.program.bindings import EnsureStateIntent
from scopecat.records.analysis import (
    AnalysisDatasetRecordOutput,
    AnalysisFactRecordOutput,
    AnalysisPublishedOutputReference,
    PublishedAnalysisRecordInput,
    RunAnalysisSubject,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.measurement import (
    InstrumentAcquisitionEvidence,
    MeasurementArray,
    MeasurementScalar,
)
from scopecat.records.parameter import TableParameterValue
from scopecat.sdk.instruments import (
    DriverAcquisition,
    DriverOutcome,
    DriverReadback,
)
from scopecat_instruments.virtual import VirtualNetworkAnalyzer
from scopecat_testkit.instrument_host import compose_test_instruments
from scopecat_testkit.server.in_process_lab import in_process_lab

from reference_lab.configuration import bootstrap_config
from reference_lab.parameters import ReadoutResonator
from reference_lab.provider import FLUX_SOURCE_ID, ReferenceLabProvider
from reference_lab.workflows.flux_spectroscopy import (
    BIAS_POINTS,
    BIAS_START,
    BIAS_STOP,
    TRACE_POINTS,
    FluxSpectroscopyDataset,
    flux_spectroscopy,
)
from reference_lab.workflows.flux_spectroscopy_analysis import (
    FLUX_SPECTROSCOPY_FIT_REVIEW_SCHEMA,
    RESONATOR_TRACE_FIT_SCHEMA,
    fit_flux_spectroscopy,
    fit_resonator_trace,
    flux_spectroscopy_analysis,
    flux_spectroscopy_fit_review,
)


class _ReferenceLabDaemon(Protocol):
    url: str


class _FluxNotebookSummary(TypedDict):
    status: str
    point_count: int
    measurement_records: int
    analysis_id: str
    analysis_revision: int
    fit_review_id: str
    fit_review_accepted: bool
    fit_report: str
    candidate_config_id: str


def test_complex_notch_fit_recovers_delay_and_ignores_one_outlier() -> None:
    frequencies_hz = np.linspace(4.96e9, 5.04e9, 601)
    resonance_hz = 5.0037e9
    linewidth_hz = 1.8e6
    amplitude = 0.84
    depth = 0.63
    delay_s = 0.8e-9
    detuning = 2.0 * (frequencies_hz - resonance_hz) / linewidth_hz
    baseline = amplitude * np.exp(
        1j * (0.42 - 2.0 * np.pi * (frequencies_hz - 5.0e9) * delay_s)
    )
    modeled_samples = cast(
        "Iterable[complex]",
        baseline * (1.0 - depth / (1.0 + 1j * detuning)),
    )
    samples = [complex(value) for value in modeled_samples]
    samples[47] += complex(0.25, -0.15)

    fit = fit_resonator_trace(
        tuple(float(value) for value in frequencies_hz),
        samples,
        dc_bias=sc.Quantity(0.1, "V"),
        temperature=sc.Quantity(20.0, "mK"),
    )

    assert float(fit.resonance_frequency.to("Hz").value) == pytest.approx(
        resonance_hz,
        abs=5.0e3,
    )
    assert float(fit.linewidth.to("Hz").value) == pytest.approx(
        linewidth_hz,
        rel=0.01,
    )
    assert fit.baseline_power == pytest.approx(amplitude**2, rel=0.005)
    assert fit.minimum_power == pytest.approx(
        (amplitude * (1.0 - depth)) ** 2,
        rel=0.02,
    )
    assert fit.complex_rmse < 0.02


def test_flux_spectroscopy_runs_fits_saves_and_proposes(tmp_path: Path) -> None:
    provider = ReferenceLabProvider(seed=7)
    config = bootstrap_config()
    composition = compose_test_instruments(config=config, provider=provider)
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )

    invocation = flux_spectroscopy()
    schema = assert_type(invocation.output, FluxSpectroscopyDataset)
    frequency_record_id = "trace/frequency"
    s_parameter_record_id = "trace/s_parameter"
    temperature_record_id = "temperature"
    definition = invocation.definition
    success_state = definition.success_state
    assert isinstance(success_state, EnsureStateIntent)
    assert [
        (assignment.property_id, assignment.value)
        for assignment in success_state.assignments
    ] == [("output_enabled", False)]
    assert [
        assignment.value
        for effect in definition.body.effects
        if isinstance(effect, EnsureStateIntent)
        for assignment in effect.assignments
        if assignment.property_id == "output_enabled"
    ] == [False, True]

    prepared = lab.prepare(invocation)
    preview = prepared.preview()
    run = prepared.run()

    assert preview.point_count == BIAS_POINTS
    assert preview.coordinate_ids == ("dc_bias",)
    assert preview.points[0].coordinates["dc_bias"] == BIAS_START
    assert preview.points[-1].coordinates["dc_bias"] == BIAS_STOP
    assert preview.primary_observables == (
        s_parameter_record_id,
        temperature_record_id,
    )
    assert preview.schema is not None
    assert preview.schema.primary_coordinates == ("dc_bias", frequency_record_id)
    dimensions = {dimension.id: dimension for dimension in preview.schema.dimensions}
    assert dimensions["shared/network_sweep.sweep/frequency"].label == "frequency"
    variables = {variable.id: variable for variable in preview.schema.variables}
    assert variables["dc_bias"].role == "coordinate"
    assert variables["dc_bias"].dims == ("point",)
    assert variables[frequency_record_id].role == "coordinate"
    assert variables[frequency_record_id].recording_group_id == "network_sweep.sweep"
    assert variables[frequency_record_id].dims == (
        "point",
        "shared/network_sweep.sweep/frequency",
    )
    assert variables[s_parameter_record_id].role == "observable"
    assert variables[s_parameter_record_id].recording_group_id == "network_sweep.sweep"
    assert variables[s_parameter_record_id].dims == (
        "point",
        "shared/network_sweep.sweep/frequency",
    )
    assert variables[temperature_record_id].role == "observable"
    assert (
        variables[temperature_record_id].recording_group_id
        == "temperature_readout.sample"
    )
    assert variables[temperature_record_id].dims == ("point",)
    preview_records = {record.id: record for record in preview.records}
    assert preview_records[frequency_record_id].role == "coordinate"
    assert (
        preview_records[frequency_record_id].recording_group_id == "network_sweep.sweep"
    )
    assert preview_records[frequency_record_id].dims == (
        "point",
        "shared/network_sweep.sweep/frequency",
    )
    assert preview_records[s_parameter_record_id].role == "observable"
    assert (
        preview_records[s_parameter_record_id].recording_group_id
        == "network_sweep.sweep"
    )
    assert preview_records[s_parameter_record_id].dims == (
        "point",
        "shared/network_sweep.sweep/frequency",
    )
    assert preview_records[temperature_record_id].role == "observable"
    assert preview_records[temperature_record_id].dims == ("point",)
    assert run.status == "completed"
    records = run.measurements().records
    assert len(records) == BIAS_POINTS
    assert all(
        set(record.coordinates) == {"dc_bias", frequency_record_id}
        and set(record.observables) == {s_parameter_record_id, temperature_record_id}
        and set(record.acquisition_evidence.variable_refs)
        == {
            frequency_record_id,
            s_parameter_record_id,
            temperature_record_id,
        }
        for record in records
    )
    for record in records:
        frequency = record.coordinates[frequency_record_id]
        assert isinstance(frequency, MeasurementArray)
        assert frequency.shape == (TRACE_POINTS,)
        assert frequency.dtype == "float64"
        assert frequency.unit == "Hz"

        s_parameter = record.observables[s_parameter_record_id]
        assert isinstance(s_parameter, MeasurementArray)
        assert s_parameter.shape == (TRACE_POINTS,)
        assert s_parameter.dtype == "complex128"
        assert s_parameter.unit == "ratio"
        assert s_parameter.values.dtype == np.dtype(np.complex128)
        assert np.iscomplexobj(s_parameter.values)

        temperature = record.observables[temperature_record_id]
        assert isinstance(temperature, MeasurementScalar)
        assert temperature.unit == "K"
    first_evidence = records[0].acquisition_evidence
    frequency_evidence = first_evidence.for_variable(frequency_record_id)
    s_parameter_evidence = first_evidence.for_variable(s_parameter_record_id)
    temperature_evidence = first_evidence.for_variable(temperature_record_id)
    assert isinstance(frequency_evidence, InstrumentAcquisitionEvidence)
    assert isinstance(s_parameter_evidence, InstrumentAcquisitionEvidence)
    assert isinstance(temperature_evidence, InstrumentAcquisitionEvidence)
    assert frequency_evidence.instrument_id == "readout-vna"
    assert frequency_evidence.result_id == "frequency"
    assert s_parameter_evidence.instrument_id == "readout-vna"
    assert s_parameter_evidence.result_id == "s_parameter"
    assert temperature_evidence.instrument_id == "mixing-chamber"
    assert temperature_evidence.result_id == "temperature"
    assert not provider.world.dc_source(
        f"{FLUX_SOURCE_ID}:flux.dac_a.ch1"
    ).output_enabled

    traces = run.measurements().traces(schema.trace.s_parameter)
    assert len(traces) == BIAS_POINTS
    assert traces[0].recording_group_id == "network_sweep.sweep"
    assert traces[0].coordinate_unit == "Hz"
    assert traces[0].observable_unit == "ratio"
    assert len(traces[0].x) == TRACE_POINTS
    assert np.iscomplexobj(traces[0].y)

    fits = fit_flux_spectroscopy(run.measurements())
    sweet_spot = max(
        fits,
        key=lambda fit: float(fit.resonance_frequency.to("Hz").value),
    )
    assert float(sweet_spot.dc_bias.to("V").value) == pytest.approx(0.0, abs=1.0e-12)
    assert float(sweet_spot.resonance_frequency.to("GHz").value) == pytest.approx(
        5.06,
        abs=0.001,
    )
    assert float(sweet_spot.linewidth.to("MHz").value) == pytest.approx(
        1.0,
        rel=0.2,
    )

    analysis = run.analyze(flux_spectroscopy_analysis())
    candidate = lab.resolve_config(config=analysis.candidate_config())
    assert analysis.id == "analysis-reference_lab-flux_spectroscopy-analysis-r1"
    assert [output.kind for output in analysis.outputs] == [
        "dataset",
        "fact",
        "table",
        "figure",
        "artifact",
        "parameter_change_proposal",
    ]
    assert analysis.executions == ()
    fit_output = analysis.output("fit-by-bias")
    assert isinstance(fit_output, AnalysisDatasetRecordOutput)
    assert fit_output.produced_by is None
    assert fit_output.derived_from is None
    fit_dataset = analysis.dataset("fit-by-bias")
    assert [field.name for field in fit_dataset.schema.fields[:4]] == [
        "dc_bias_v",
        "temperature_mK",
        "resonance_frequency_ghz",
        "linewidth_mhz",
    ]
    selected_output = analysis.output("selected-sweet-spot")
    assert isinstance(selected_output, AnalysisFactRecordOutput)
    assert selected_output.produced_by is None
    selected_fit = analysis.fact_as(
        "selected-sweet-spot",
        RESONATOR_TRACE_FIT_SCHEMA,
    )
    assert selected_fit.model_id == "reference_lab.complex_s21_notch.v1"
    fit_table = analysis.table("fit-by-bias-table")
    assert fit_table.source is not None
    assert fit_table.source.output_id == "fit-by-bias"
    [proposal] = analysis.parameter_proposals
    assert proposal.evidence_output_ids == (
        "selected-sweet-spot",
        "fit-by-bias",
    )
    report = run.published_analysis(analysis.id).artifact("fit-report")
    assert report.entry.filename == "flux-spectroscopy-fit.md"
    assert report.text().startswith("# Resonator flux spectroscopy fit\n")
    assert f"Fitted bias points: {BIAS_POINTS}" in report.text()
    fitted_frequency = _readout_quantity(
        candidate, ReadoutResonator.resonance_frequency.name
    )
    fitted_linewidth = _readout_quantity(candidate, ReadoutResonator.linewidth.name)
    assert float(fitted_frequency.to("GHz").value) == pytest.approx(5.06, abs=0.001)
    assert float(fitted_linewidth.to("MHz").value) == pytest.approx(1.0, rel=0.2)
    active_frequency = _readout_quantity(
        lab.resolve_config(), ReadoutResonator.resonance_frequency.name
    )
    assert float(active_frequency.to("GHz").value) == pytest.approx(5.0)

    review = run.analyze(flux_spectroscopy_fit_review())
    assert review.id == "analysis-reference_lab-flux_spectroscopy-fit-review-r1"
    quality = review.fact_as(
        "quality-review",
        FLUX_SPECTROSCOPY_FIT_REVIEW_SCHEMA,
    )
    assert quality.accepted
    assert quality.worst_complex_rmse < 0.02
    [review_input] = review.inputs
    assert isinstance(review_input, PublishedAnalysisRecordInput)
    assert review_input.source == AnalysisPublishedOutputReference(
        subject=RunAnalysisSubject(run_id=run.id),
        analysis_record_id=analysis.id,
        output_id="fit-by-bias",
    )
    assert review_input.target == fit_output.content.dataset_id
    assert review.executions == ()


def test_direct_control_notebook_completes_through_the_project_daemon(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    result = run_path(
        str(Path(__file__).parents[1] / "notebooks" / "10_direct_control.py")
    )

    inventory = cast("list[tuple[str, str]]", result["inventory"])
    trace_results = cast("dict[str, dict[str, object]]", result["trace_results"])
    assert {instrument_id for instrument_id, _availability in inventory} >= {
        "bench-source",
        "mixing-chamber",
        "readout-vna",
    }
    assert trace_results["frequency"]["shape"] == [201]
    assert trace_results["s_parameter"]["shape"] == [201]


def test_flux_spectroscopy_notebook_completes_through_the_project_daemon(
    reference_lab_daemon: _ReferenceLabDaemon,
) -> None:
    assert reference_lab_daemon.url.startswith("http://127.0.0.1:")
    result = run_path(
        str(Path(__file__).parents[1] / "notebooks" / "20_flux_spectroscopy.py")
    )

    summary = cast("_FluxNotebookSummary", result["summary"])
    assert summary["status"] == "completed"
    assert summary["point_count"] == BIAS_POINTS
    assert summary["measurement_records"] == BIAS_POINTS
    assert summary["analysis_id"] == (
        "analysis-reference_lab-flux_spectroscopy-analysis-r1"
    )
    assert summary["analysis_revision"] == 1
    assert summary["fit_review_id"] == (
        "analysis-reference_lab-flux_spectroscopy-fit-review-r1"
    )
    assert summary["fit_review_accepted"]
    assert summary["fit_report"] == "flux-spectroscopy-fit.md"
    assert summary["candidate_config_id"] == "candidate-readout-resonator-fit"


def test_flux_spectroscopy_failure_aborts_with_bias_disabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_collect(
        _driver: VirtualNetworkAnalyzer,
        _request: DriverAcquisition,
    ) -> DriverOutcome[DriverReadback]:
        raise RuntimeError("injected VNA acquisition failure")

    monkeypatch.setattr(VirtualNetworkAnalyzer, "collect", fail_collect)
    provider = ReferenceLabProvider(seed=7)
    config = bootstrap_config()
    composition = compose_test_instruments(config=config, provider=provider)
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )

    with pytest.raises(RunIndeterminate):
        lab.prepare(flux_spectroscopy()).run()

    [run] = lab.runs().items
    assert run.status == "unknown"
    assert not provider.world.dc_source(
        f"{FLUX_SOURCE_ID}:flux.dac_a.ch1"
    ).output_enabled


def _readout_quantity(
    config: ConfigProfileSnapshot,
    field_id: str,
) -> sc.Quantity:
    value = config.parameter_snapshot.get("readout_resonators")
    assert isinstance(value, TableParameterValue)
    row = next(
        item
        for item in value.rows
        if item["resonator"] == sc.EntityRef(id="q0", kind="logical_qubit")
    )
    selected = row[field_id]
    assert isinstance(selected, sc.Quantity)
    return selected
