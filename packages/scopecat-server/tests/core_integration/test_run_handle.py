# pyright: reportUnknownMemberType=false

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Protocol, cast

import pytest
import scopecat as sc
import scopecat.authoring as authoring
from scopecat.api.run import RunHandle
from scopecat.authoring import (
    Experiment,
    ExperimentInvocation,
)
from scopecat.kernel.quantity import Quantity
from scopecat.measurements.results import Dataset
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.system import ExperimentSystem
from scopecat.records.config import config_content_hash
from scopecat.records.measurement import MeasurementRecord, MeasurementScalar
from scopecat.records.run import AnalysisCandidateRunConfigSource
from scopecat.sdk.instruments import InterfaceRef
from scopecat_testkit.authoring import DRIVE_FREQUENCY_POINT
from scopecat_testkit.instrument_host import compose_test_instruments
from scopecat_testkit.server.in_process_lab import in_process_lab
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider
from scopecat_testkit.workflow_fixtures import load_config, load_invocation

_SET_FREQUENCY = InterfaceRef("test.set_frequency/v1")
_SET_FREQUENCY_VALUE = _SET_FREQUENCY.property("frequency")
_SCALAR_SIGNAL = InterfaceRef("test.scalar_signal/v1")
_SCALAR_SIGNAL_VALUE = _SCALAR_SIGNAL.acquisition("sample").result("signal")


class _ArrowColumn(Protocol):
    def to_pylist(self) -> list[object]: ...


class _ArrowSchema(Protocol):
    @property
    def names(self) -> list[str]: ...


class _ArrowRecordBatch(Protocol):
    @property
    def num_rows(self) -> int: ...

    @property
    def schema(self) -> _ArrowSchema: ...

    def __getitem__(self, name: str) -> _ArrowColumn: ...


class _ArrowRecordBatchReader(Protocol):
    @property
    def schema(self) -> _ArrowSchema: ...

    def __iter__(self) -> Iterator[_ArrowRecordBatch]: ...


@dataclass(frozen=True, slots=True)
class _ComputeOnlyResult:
    score: sc.ValueRef[float]


@dataclass(frozen=True, slots=True)
class _ConvertedQuantityResult:
    voltage: sc.ValueRef[sc.Quantity]


@dataclass(frozen=True, slots=True)
class _StructuredComputeResult(sc.ProductBundle):
    doubled: Annotated[sc.DataRef[int], sc.ScalarType(sc.IntType())]
    label: Annotated[sc.DataRef[str], sc.ScalarType(sc.StringType())]


@_StructuredComputeResult.kernel
def _structured_compute(*, value: int) -> tuple[int, str]:
    return value * 2, f"value-{value}"


@authoring.module(id="test.session.simple_frequency_scan")
def SIMPLE_FREQUENCY_SCAN(
    module: authoring.ModuleContext,
    frequency: Annotated[
        authoring.Input[Quantity],
        authoring.ScalarType(authoring.QuantityType(unit="GHz")),
    ],
) -> authoring.ProductRef:
    source = module._resource(
        "source",
        requires=(_SET_FREQUENCY, _SCALAR_SIGNAL),
    )
    module._bind_property(
        source,
        _SET_FREQUENCY_VALUE,
        value=frequency,
    )
    signal = module._product("signal", unit="ratio")
    module._acquire(
        "read-signal",
        resource=source,
        results={_SCALAR_SIGNAL_VALUE: signal},
    )
    return signal


def _quantity_coordinate(record: MeasurementRecord, coordinate_id: str) -> Quantity:
    value = record.coordinates[coordinate_id]
    assert isinstance(value, MeasurementScalar)
    assert value.dtype in {"float64", "int64"}
    assert isinstance(value.value, int | float) and not isinstance(value.value, bool)
    assert value.unit is not None
    return Quantity(float(value.value), value.unit)


def simple_frequency_scan(*, subject: str) -> ExperimentInvocation:
    return simple_frequency_scan_experiment().bind(subject=subject)


def simple_frequency_scan_experiment() -> Experiment[...]:
    def definition(
        experiment: authoring.ExperimentContext,
        subject: Annotated[
            authoring.Input[sc.EntityRef | str],
            authoring.EntityType(),
        ],
    ) -> None:
        del subject
        signal = experiment.use(SIMPLE_FREQUENCY_SCAN(frequency=DRIVE_FREQUENCY_POINT))
        experiment.grid(
            sc.axis(
                DRIVE_FREQUENCY_POINT,
                center=authoring.parameter(
                    "drive_frequency",
                    authoring.ScalarType(authoring.QuantityType()),
                ),
                span=Quantity(value=200.0, unit="MHz"),
                points=3,
            ),
        )
        experiment.alias(signal, record_id="signal")

    return authoring.experiment(
        id="test.session.simple_frequency_scan",
        kind="simple_frequency_scan",
    )(definition)


def test_in_process_lab_runs_experiment_spec(tmp_path: Path) -> None:
    config = load_config()
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )

    preview = lab.prepare(load_invocation()).preview()

    assert preview.point_count == 3
    assert preview.primary_observables == ("signal",)
    assert [
        (binding.id, binding.kind, binding.owner, binding.origin)
        for binding in preview.bindings
    ] == [
        ("subject", "input", "invocation", "override"),
        ("drive_frequency", "coordinate", "point-plan", "around"),
        ("drive_frequency", "parameter", "configuration", None),
    ]
    [edge] = preview.binding_edges
    assert (edge.source.kind, edge.source.id) == (
        "parameter",
        "drive_frequency",
    )
    assert (edge.relation, edge.target.kind, edge.target.id) == (
        "centers",
        "coordinate",
        "drive_frequency",
    )


def test_in_process_lab_records_compute_value_without_instruments(
    tmp_path: Path,
) -> None:
    @sc.experiment(id="test.session.compute-only", kind="compute-only")
    def compute_only(experiment: sc.ExperimentContext) -> _ComputeOnlyResult:
        selected_score = 2.5

        def calculate_score() -> float:
            return selected_score

        score = cast(
            "sc.ValueRef[float]",
            experiment.compute(fn=calculate_score),
        )
        return _ComputeOnlyResult(score=score)

    config = load_config()
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=ExperimentSystem(
            instrument_catalog=InstrumentContractCatalog(
                config_content_hash=config_content_hash(config)
            )
        ),
    )

    preview = lab.prepare(compute_only).preview()
    assert preview.host_compute_ids == ("calculate_score",)
    assert preview.observation_compute_ids == ()
    [compute] = preview.computes
    assert compute.inputs == ()
    assert compute.outputs == ("calculate_score",)
    assert compute.demanded_by == ("record:score",)
    assert compute.implementation.startswith("python:")
    assert not compute.deterministic
    assert compute.captures == ("selected_score",)

    run = lab.prepare(compute_only).run()
    dataset = run.measurements()
    stored_result = run.result()
    typed_result = run.result(compute_only.build().output)

    assert run.status == "completed"
    assert isinstance(dataset, Dataset)
    assert dataset.entry.id == "raw-measurements"
    [record] = dataset.records
    assert record.observables["score"] == MeasurementScalar.create(
        dtype="float64",
        value=2.5,
    )
    variable = next(item for item in dataset.schema.variables if item.id == "score")
    assert variable.source_value_id == "calculate_score"
    assert stored_result.contract.id == "test.session.compute-only"
    assert stored_result.paths == (("score",),)
    assert stored_result[0].value("score") == 2.5
    assert typed_result[0].value(typed_result.output.score) == 2.5
    datasets = run.contents(role="dataset")
    assert tuple(entry.id for entry in datasets.items) == ("raw-measurements",)
    assert tuple(entry.id for entry in datasets.items) == (dataset.entry.id,)
    assert run.content("dataset", dataset.entry.id) == dataset.entry


def test_in_process_lab_records_returned_scan_without_instruments(
    tmp_path: Path,
) -> None:
    @sc.experiment(id="test.session.coordinate-only", kind="coordinate-only")
    def coordinate_only(
        experiment: sc.ExperimentContext,
    ) -> sc.CoordinateRef[int]:
        return experiment.scan("value", (1, 2, 3))

    config = load_config()
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=ExperimentSystem(
            instrument_catalog=InstrumentContractCatalog(
                config_content_hash=config_content_hash(config)
            )
        ),
    )

    run = lab.prepare(coordinate_only).run()
    dataset = run.measurements()
    records = dataset.records
    stored_result = run.result()
    typed_result = run.result(coordinate_only.build().output)

    assert run.status == "completed"
    assert dataset.schema.primary_coordinates == ("value",)
    assert dataset.schema.primary_observables == ()
    assert tuple(record.coordinates["value"] for record in records) == (
        MeasurementScalar.create(dtype="int64", value=1),
        MeasurementScalar.create(dtype="int64", value=2),
        MeasurementScalar.create(dtype="int64", value=3),
    )
    assert stored_result.paths == (("result",),)
    assert tuple(point.value("result") for point in stored_result) == (1, 2, 3)
    assert tuple(point.value(typed_result.output) for point in typed_result) == (
        1,
        2,
        3,
    )


def test_structured_host_compute_is_one_public_compute(tmp_path: Path) -> None:
    @sc.experiment(id="test.session.structured-compute")
    def structured(experiment: sc.ExperimentContext) -> _StructuredComputeResult:
        return experiment.compute(fn=_structured_compute, value=3)

    config = load_config()
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=ExperimentSystem(
            instrument_catalog=InstrumentContractCatalog(
                config_content_hash=config_content_hash(config)
            )
        ),
    )

    prepared = lab.prepare(structured.build())
    preview = prepared.preview()
    [compute] = preview.computes
    assert compute.id == "structured_compute"
    assert compute.placement == "host"
    assert compute.inputs == ("value",)
    assert compute.outputs == ("doubled", "label")
    assert compute.demanded_by == ("record:doubled", "record:label")

    run = prepared.run()
    result = run.result(structured.build().output)
    [point] = result
    assert point.value(result.output.doubled) == 6
    assert point.value(result.output.label) == "value-3"


def test_host_unit_conversion_is_recordable_and_visible_in_preview(
    tmp_path: Path,
) -> None:
    def source_voltage() -> Annotated[
        sc.Quantity,
        sc.ScalarType(sc.QuantityType(unit="V")),
    ]:
        return sc.Quantity(0.125, "V")

    @sc.experiment(id="test.session.converted-quantity")
    def converted(experiment: sc.ExperimentContext) -> _ConvertedQuantityResult:
        voltage = cast(
            "sc.ValueRef[sc.Quantity]",
            experiment.compute(fn=source_voltage),
        )
        return _ConvertedQuantityResult(
            voltage=experiment.convert(voltage, "mV"),
        )

    config = load_config()
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=ExperimentSystem(
            instrument_catalog=InstrumentContractCatalog(
                config_content_hash=config_content_hash(config)
            )
        ),
    )

    prepared = lab.prepare(converted.build())
    preview = prepared.preview()
    assert preview.host_compute_ids == ("source_voltage", "convert_unit_value")
    assert preview.observation_compute_ids == ()
    assert preview.computes[1].inputs == (
        "value",
        "source_unit",
        "target_unit",
    )

    result = prepared.run().result(converted.build().output)
    assert result[0].value(result.output.voltage) == sc.Quantity(125.0, "mV")


def test_empty_measurement_reader_preserves_the_projected_schema(
    tmp_path: Path,
) -> None:
    @sc.experiment(id="test.session.empty-points", kind="empty-points")
    def empty_points(experiment: sc.ExperimentContext) -> None:
        experiment.points((), coordinates=(DRIVE_FREQUENCY_POINT,))
        experiment.alias(DRIVE_FREQUENCY_POINT, record_id="observed_frequency")

    config = load_config()
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=ExperimentSystem(
            instrument_catalog=InstrumentContractCatalog(
                config_content_hash=config_content_hash(config)
            )
        ),
    )
    run = lab.prepare(empty_points).run()

    reader = cast(
        "_ArrowRecordBatchReader",
        run.measurements()
        .project(
            {"frequency": "drive_frequency"},
            diagnostics="reason",
        )
        .to_record_batch_reader(batch_size=2),
    )
    assert reader.schema.names == [
        "point_index",
        "logical_point_id",
        "frequency",
        "frequency__unavailable_reason",
    ]
    assert list(reader) == []


def test_run_projects_paged_measurements_into_one_arrow_reader(tmp_path: Path) -> None:
    run = _run_signal_scan(tmp_path)

    reader = cast(
        "_ArrowRecordBatchReader",
        run.measurements()
        .project(
            {
                "frequency": "drive_frequency",
                "response": "signal",
            },
            diagnostics="reason",
        )
        .to_record_batch_reader(batch_size=2),
    )
    batches = list(reader)

    assert reader.schema.names == [
        "point_index",
        "logical_point_id",
        "frequency",
        "frequency__unavailable_reason",
        "response",
        "response__unavailable_reason",
    ]
    assert [batch.num_rows for batch in batches] == [2, 1]
    assert all(batch.schema.names == reader.schema.names for batch in batches)
    assert [
        point for batch in batches for point in batch["point_index"].to_pylist()
    ] == [0, 1, 2]
    assert all(
        reason is None
        for batch in batches
        for reason in batch["response__unavailable_reason"].to_pylist()
    )
    with pytest.raises(ValueError, match="between 1 and 500"):
        run.measurements().project().to_record_batch_reader(batch_size=0)
    with pytest.raises(ValueError, match="between 1 and 500"):
        run.measurements().project().to_record_batch_reader(batch_size=501)


def _run_signal_scan(tmp_path: Path) -> RunHandle:
    config = load_config()
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )
    return lab.prepare(load_invocation()).run()


def test_in_process_lab_closed_loop_uses_notebook_first_candidate_config(
    tmp_path: Path,
) -> None:
    config = load_config()
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )
    experiment = load_invocation()

    baseline = lab.prepare(experiment).run()
    context = baseline.analysis("manual best signal")
    dataset = context.measurements()
    analysis = context.result().propose(
        "drive_frequency",
        sc.replace_scalar_parameter(
            "drive_frequency",
            _quantity_coordinate(
                dataset.records[2],
                "drive_frequency",
            ),
        ),
        reason="manual notebook pick",
    )
    outcome = analysis.save()
    candidate_config = outcome.candidate_config()
    candidate = lab.prepare(experiment, config=candidate_config).run()

    assert baseline.id.startswith("run_")
    assert dataset.entry.id == "raw-measurements"
    assert [input_ref.target for input_ref in outcome.inputs] == ["raw-measurements"]
    assert not any(
        record.kind == "candidate_config"
        for record in baseline.contents(role="record").items
    )
    assert candidate.status == "completed"
    source = candidate.snapshot.config_source
    assert isinstance(source, AnalysisCandidateRunConfigSource)
    assert source.source_run_id == baseline.id
    assert source.analysis_record_id == outcome.id
    assert source.proposal_id == candidate_config.proposal_id


def test_in_process_provider_closed_loop_uses_candidate_config_shortcut(
    tmp_path: Path,
) -> None:
    config = load_config()
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )
    experiment = load_invocation()

    baseline = lab.prepare(experiment).run()
    context = baseline.analysis("manual center point")
    dataset = context.measurements()
    analysis = context.result().propose(
        "drive_frequency",
        sc.replace_scalar_parameter(
            "drive_frequency",
            _quantity_coordinate(
                dataset.records[2],
                "drive_frequency",
            ),
        ),
        reason="manual center point",
    )
    outcome = analysis.save()
    candidate_config = outcome.candidate_config()
    candidate = lab.prepare(experiment, config=candidate_config).run()

    assert baseline.status == "completed"
    assert len(dataset.records) == 3
    assert dataset.entry.id == "raw-measurements"
    assert (
        candidate_config.parameter_proposal.deltas[0].parameter_id == "drive_frequency"
    )
    assert candidate.status == "completed"
    source = candidate.snapshot.config_source
    assert isinstance(source, AnalysisCandidateRunConfigSource)
    assert source.analysis_record_id == outcome.id
