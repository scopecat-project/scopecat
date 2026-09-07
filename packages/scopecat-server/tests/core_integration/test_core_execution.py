from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Annotated, Never, cast

import pytest
import scopecat as sc
from pydantic import JsonValue
from scopecat.compiler.bound_facts import (
    LogicalResourceRequirement,
    record_product,
)
from scopecat.compiler.point_domain import PointDomain
from scopecat.compiler.relations.verification import (
    ExpressionTypeBindings,
    RowType,
)
from scopecat.config.environment import build_config_environment
from scopecat.execution.evidence import (
    instrument_state_evidence_ref,
)
from scopecat.execution.local.program import CollectOperation
from scopecat.execution.program import RunHostBinding
from scopecat.kernel.errors import ProviderContractError, RunFinalizationFailed
from scopecat.kernel.problems import (
    Problem,
    ProblemPhase,
    model_location,
)
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.resource_identity import logical_resource_port_id
from scopecat.kernel.state import StateValue
from scopecat.kernel.symbols import SymbolId
from scopecat.kernel.value_types import Payload, Scalar, String, TableColumn
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Table as TableType
from scopecat.planning.provider_binding import (
    resolve_instrument_contract_catalog,
    validate_run_host_binding,
)
from scopecat.program.expressions import (
    lit,
    point_col,
)
from scopecat.program.logical import (
    ImplementationId,
    LocalPythonImplementation,
)
from scopecat.program.point_domain import point_axis_values
from scopecat.program.value_graph import (
    ComputeOutput,
    OperationId,
    operation_result_id,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.execution import InstrumentStateEvidence
from scopecat.records.instrument import (
    CommandChannelBinding,
    InstrumentStateSnapshot,
    state_member_target,
    state_observation,
)
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.run import RunSnapshot
from scopecat.runs.access import dataset_storage_ref
from scopecat.runs.repository import TerminalRunCommit
from scopecat.sdk.instruments import DriverPayload, InterfaceRef, PropertyRef
from scopecat.sdk.instruments.commands import (
    CollectAxisRequest,
    InstrumentStateAssignment,
    InstrumentStateCommand,
)
from scopecat.sdk.instruments.contracts import (
    AcquisitionSpec,
    InstrumentDescription,
    InterfaceSpec,
    PropertySpec,
    acquisition_axis,
)
from scopecat.sdk.instruments.execution import RunInstrumentHost
from scopecat.sdk.instruments.provider import (
    InstrumentConnectionContext,
    InstrumentProvider,
    InstrumentProviderContext,
    InstrumentProviderDescription,
)
from scopecat_testkit.authoring import bind_invocation
from scopecat_testkit.bound_program import (
    ComputeNodeFixture,
    ProgramFixture,
    bind_program_facts,
    compute_result,
    instrument_acquisition,
    instrument_invocation,
    observable_product,
    program_fixture,
)
from scopecat_testkit.expressions import state_property, verified_scalar_expr
from scopecat_testkit.instrument_drivers import SignalInstrumentDriver
from scopecat_testkit.local_materialization import (
    LocalEffectInspection,
    materialize_local_execution,
    operations_of_type,
)
from scopecat_testkit.materialized_effects import config_with_physical_resources
from scopecat_testkit.payload_codecs import json_payload_codecs
from scopecat_testkit.records import (
    assert_model_round_trip,
)
from scopecat_testkit.server.execution import execute_bound_run
from scopecat_testkit.server.runtime import (
    sqlite_execution_session,
    sqlite_run_repository,
)
from scopecat_testkit.signal_instruments import (
    TestSignalInstrument,
)
from scopecat_testkit.workflow_fixtures import load_config, load_experiment

from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


def test_execution_builds_one_bound_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_count = 0

    def counted_session(
        project_root: str | Path,
        run_id: str,
        *,
        runs: SQLiteRunRepository | None = None,
        instruments: RunInstrumentHost | None = None,
    ):
        nonlocal session_count
        session_count += 1
        return sqlite_execution_session(
            project_root,
            run_id,
            runs=runs,
            instruments=instruments,
        )

    monkeypatch.setattr(
        "scopecat_testkit.server.execution.sqlite_execution_session",
        counted_session,
    )

    execute_bound_run(
        config=load_config(),
        experiment=load_experiment(),
        instruments=[TestSignalInstrument()],
        project_root=tmp_path,
    )

    assert session_count == 1


def test_instrument_models_round_trip() -> None:
    description = InstrumentDescription(
        instrument_id="source-0",
        implementation_id="test.instrument",
        implementation_version="v1",
        interfaces=[
            InterfaceSpec(
                id="test.set_frequency/v1",
                properties=[
                    PropertySpec(
                        id="frequency",
                        value_type=Scalar(QuantityType(unit="GHz")),
                    )
                ],
            )
        ],
    )
    state_value = StateValue(Quantity(value=5.0, unit="GHz"))
    state = InstrumentStateSnapshot(
        instrument_id="source-0",
        observations=[
            state_observation(
                PropertyRef("test.set_frequency/v1", (), "frequency"),
                state_value,
            )
        ],
    )
    command = InstrumentStateCommand(
        command_id="round-trip-state",
        instrument_id="source-0",
        assignments=[
            InstrumentStateAssignment(
                resource_id="source-0",
                target=state_member_target(
                    PropertyRef("test.set_frequency/v1", (), "frequency")
                ),
                value=state_value,
                entity_ids=["q0"],
                channel_bindings=[
                    CommandChannelBinding(
                        entity_id="q0",
                        channel_id="drive.awg0.ch1",
                        interface_id="test.set_frequency/v1",
                    )
                ],
            )
        ],
    )

    assert_model_round_trip(
        description,
    )
    assert_model_round_trip(
        state,
    )
    assert_model_round_trip(
        command,
    )


def test_instrument_state_observation_rejects_non_durable_metadata() -> None:
    with pytest.raises(ValueError, match="valid JSON value"):
        state_observation(
            InterfaceRef("test.source/v1").property("frequency"),
            StateValue(1.0),
            metadata={"opaque": cast("JsonValue", object())},
        )


def test_run_persists_measurements_and_run_files(
    tmp_path: Path,
) -> None:
    config = load_config()
    snapshot = execute_bound_run(
        config=config,
        experiment=load_experiment(),
        instruments=[TestSignalInstrument()],
        project_root=tmp_path,
    )

    repository = sqlite_run_repository(tmp_path)
    assert snapshot.status == "completed"
    records = repository.list_contents(
        snapshot.run_id,
        limit=100,
        role="record",
    ).items
    datasets = repository.list_contents(
        snapshot.run_id,
        limit=100,
        role="dataset",
    ).items
    assert {record.id for record in records} == {"instrument-state-evidence"}
    assert {dataset.id for dataset in datasets} == {"raw-measurements"}
    raw_dataset = datasets[0]
    assert raw_dataset.kind == "measurement_dataset"
    persisted_snapshot = repository.read_snapshot(snapshot.run_id)
    persisted_config = repository.read_config_profile_snapshot(snapshot.run_id)
    state_evidence = repository.read_model(
        snapshot.run_id,
        instrument_state_evidence_ref(),
        InstrumentStateEvidence,
    )
    assert persisted_snapshot == snapshot
    assert persisted_config == config
    assert {
        snapshot.instrument_id
        for snapshot in [
            *state_evidence.observed_state,
            *state_evidence.baseline_state,
            *state_evidence.final_state,
        ]
    } == {"source-0"}
    assert state_evidence.observed_state == state_evidence.baseline_state
    final_state_value = state_evidence.final_state[0].observations[0].value.root
    assert final_state_value == Quantity(value=5.1, unit="GHz")
    persisted_state_evidence = state_evidence.model_dump(mode="json")
    assert persisted_state_evidence["final_state"][0]["observations"][0]["value"] == {
        "value": 5.1,
        "unit": "GHz",
    }
    assert not repository.exists(snapshot.run_id, "experiment-plan.json")
    assert not repository.exists(
        snapshot.run_id,
        "records/device_program/device-program.json",
    )

    measurements = sqlite_run_repository(tmp_path).read_measurement_records(
        snapshot.run_id,
        dataset_storage_ref(raw_dataset),
    )
    assert [item.point_index for item in measurements] == [0, 1, 2]
    drive_frequencies: list[float] = []
    for item in measurements:
        drive_frequency = item.coordinates["drive_frequency"]
        assert isinstance(drive_frequency, MeasurementScalar)
        assert not isinstance(drive_frequency.value, bool)
        assert isinstance(drive_frequency.value, int | float)
        drive_frequencies.append(float(drive_frequency.value))
    assert drive_frequencies == [
        4.9,
        5.0,
        5.1,
    ]
    signal_values: list[float] = []
    for measurement in measurements:
        signal = measurement.observables["signal"]
        assert isinstance(signal, MeasurementScalar)
        assert not isinstance(signal.value, bool)
        assert isinstance(signal.value, int | float)
        signal_values.append(float(signal.value))
    assert signal_values == [
        0.5,
        1.0,
        0.5,
    ]


def test_terminal_commit_does_not_publish_snapshot_after_content_write_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pending_ref = instrument_state_evidence_ref()

    def fail_terminal_commit(
        _storage: SQLiteRunRepository,
        _commit: TerminalRunCommit,
    ) -> RunSnapshot:
        raise OSError("injected terminal persistence failure")

    monkeypatch.setattr(
        SQLiteRunRepository,
        "commit_terminal",
        fail_terminal_commit,
    )

    with pytest.raises(RunFinalizationFailed) as failed:
        execute_bound_run(
            config=load_config(),
            experiment=load_experiment(),
            instruments=[TestSignalInstrument()],
            project_root=tmp_path,
        )

    assert failed.value.terminal_persistence == "unconfirmed"
    assert isinstance(failed.value.__cause__, OSError)
    assert "injected terminal persistence" in str(failed.value.__cause__)
    assert failed.value.finalization_problems[0].code == "run_terminal_commit_failed"
    storage = sqlite_run_repository(tmp_path)
    manifest = storage.list_runs()[0]
    assert manifest.outcome is None
    assert not storage.exists(manifest.run_id, pending_ref)


class _OrderedAbiProblemProvider:
    provider_id = "tests.ordered_abi_provider"

    def __init__(self) -> None:
        self.connect_called = False

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        del context
        source_description = TestSignalInstrument().describe()
        source_description = source_description.model_copy(
            update={
                "interfaces": [
                    interface
                    for interface in source_description.interfaces
                    if interface.id != "test.scalar_signal/v1"
                ]
            }
        )
        unknown_description = (
            TestSignalInstrument()
            .describe()
            .model_copy(update={"instrument_id": "not-in-config"})
        )
        return InstrumentProviderDescription(
            provider_id="tests.different_provider_id",
            instruments=(source_description, unknown_description),
            problems=(
                Problem(
                    code="provider_abi_warning",
                    phase=ProblemPhase.PROVIDER_PREFLIGHT,
                    message="provider ABI warning",
                    location=model_location("instrument_provider", "description"),
                ),
            ),
        )

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> Never:
        del context
        self.connect_called = True
        raise AssertionError("preflight must not connect an instrument")


class _PartialDescriptionProvider:
    provider_id = "tests.partial_description_provider"

    def __init__(self) -> None:
        self.connect_called = False

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        del context
        description = (
            TestSignalInstrument()
            .describe()
            .model_copy(update={"instrument_id": "not-in-config"})
        )
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=(description,),
        )

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> Never:
        del context
        self.connect_called = True
        raise AssertionError("preflight must not connect an instrument")


class _FailingDescriptionProvider:
    provider_id = "tests.failing_description_provider"

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.connect_called = False

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        del context
        raise self.error

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> Never:
        del context
        self.connect_called = True
        raise AssertionError("preflight must not connect an instrument")


class _UnitAbiProvider:
    provider_id = "tests.unit_abi_provider"

    def __init__(
        self,
        *,
        result_unit: str | None,
        axis_unit: str | None = None,
        include_axis: bool = False,
    ) -> None:
        self.result_unit = result_unit
        self.axis_unit = axis_unit
        self.include_axis = include_axis
        self.connect_called = False

    def describe(
        self,
        context: InstrumentProviderContext,
    ) -> InstrumentProviderDescription:
        del context
        description = TestSignalInstrument().describe()
        interfaces: list[InterfaceSpec] = []
        for interface in description.interfaces:
            if interface.id != "test.scalar_signal/v1":
                interfaces.append(interface)
                continue
            acquisition = interface.acquisitions[0]
            assert isinstance(acquisition, AcquisitionSpec)
            advertised_result = acquisition.results[0].model_copy(
                update={
                    "unit": self.result_unit,
                    "axes": (
                        [
                            acquisition_axis(
                                "sample",
                                kind="sample",
                                size=2,
                                unit=self.axis_unit,
                            )
                        ]
                        if self.include_axis
                        else []
                    ),
                }
            )
            interfaces.append(
                interface.model_copy(
                    update={
                        "acquisitions": [
                            acquisition.model_copy(
                                update={"results": [advertised_result]}
                            )
                        ]
                    }
                )
            )
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=(description.model_copy(update={"interfaces": interfaces}),),
        )

    def connect(
        self,
        context: InstrumentConnectionContext,
    ) -> Never:
        del context
        self.connect_called = True
        raise AssertionError("preflight must not connect an instrument")


def _lower_test_host_binding(
    plan: LocalEffectInspection,
    config: ConfigProfileSnapshot,
    provider: InstrumentProvider,
    *,
    planning_problems: tuple[Problem, ...] = (),
):
    catalog = resolve_instrument_contract_catalog(
        config=config,
        provider_id=provider.provider_id,
        describe=provider.describe,
    )
    if catalog.problems and not catalog.instruments:
        raise ProviderContractError((*planning_problems, *catalog.problems))
    provider_id = catalog.provider_id
    if provider_id is None:
        raise AssertionError("provider resolution must retain its identity")
    program = RunHostBinding(
        resource_order=plan.resource_order,
        provider_id=provider_id,
        advertised_descriptions={
            description.instrument_id: description
            for description in catalog.instruments
        },
    )
    return validate_run_host_binding(
        host=program,
        effect_blocks=(tuple(effect.operation for effect in plan.effects),),
        problems=(*planning_problems, *catalog.problems),
    )


def test_provider_abi_problems_are_aggregated_in_stable_order_before_run(
    tmp_path: Path,
) -> None:
    provider = _OrderedAbiProblemProvider()
    config = load_config()
    plan = materialize_local_execution(
        bind_program_facts(load_experiment(), build_config_environment(config))
    )
    plan = _first_point_plan(plan)
    planning_problems = (
        Problem(
            code="plan_warning",
            phase=ProblemPhase.PLANNING,
            message="materialized local semantics warning",
            location=model_location("materialized_effects"),
        ),
    )

    with pytest.raises(ProviderContractError) as captured:
        _lower_test_host_binding(
            plan,
            config,
            provider,
            planning_problems=planning_problems,
        )

    assert [problem.code for problem in captured.value.problems] == [
        "plan_warning",
        "provider_abi_warning",
        "instrument_provider_id_mismatch",
        "instrument_not_in_config",
        "instrument_acquisition_result_unsupported",
    ]
    assert not provider.connect_called
    assert sqlite_run_repository(tmp_path).list_runs() == []


def test_partial_provider_description_reports_missing_bound_instrument_before_run(
    tmp_path: Path,
) -> None:
    provider = _PartialDescriptionProvider()
    config = load_config()
    plan = materialize_local_execution(
        bind_program_facts(load_experiment(), build_config_environment(config))
    )

    with pytest.raises(ProviderContractError) as captured:
        _lower_test_host_binding(plan, config, provider)

    assert [problem.code for problem in captured.value.problems] == [
        "instrument_not_in_config",
        "missing_instrument_description",
    ]
    assert not provider.connect_called
    assert sqlite_run_repository(tmp_path).list_runs() == []


def test_provider_description_exception_fails_at_preflight_boundary(
    tmp_path: Path,
) -> None:
    failure = RuntimeError("description unavailable")
    provider = _FailingDescriptionProvider(failure)
    config = load_config()
    plan = materialize_local_execution(
        bind_program_facts(load_experiment(), build_config_environment(config))
    )
    with pytest.raises(ProviderContractError) as captured:
        _lower_test_host_binding(plan, config, provider)

    assert [problem.code for problem in captured.value.problems] == [
        "instrument_provider_description_failed"
    ]
    assert captured.value.__cause__ is None
    assert not provider.connect_called
    assert sqlite_run_repository(tmp_path).list_runs() == []


@pytest.mark.parametrize("advertised_unit", [None, "GHz"])
def test_provider_acquisition_result_unit_mismatch_is_rejected_before_run(
    tmp_path: Path,
    advertised_unit: str | None,
) -> None:
    provider = _UnitAbiProvider(result_unit=advertised_unit)
    config = load_config()
    plan = materialize_local_execution(
        bind_program_facts(load_experiment(), build_config_environment(config))
    )
    plan = _first_point_plan(plan)

    with pytest.raises(ProviderContractError) as captured:
        _lower_test_host_binding(plan, config, provider)

    problem = captured.value.problems[0]
    assert len(captured.value.problems) == 1
    assert problem.code == "instrument_acquisition_result_unit_mismatch"
    assert problem.location == model_location(
        "execution_program",
        "operations",
        operations_of_type(plan, CollectOperation, point_index=0)[0].operation_id,
        "requests",
        "signal",
        "unit",
    )
    assert not provider.connect_called
    assert sqlite_run_repository(tmp_path).list_runs() == []


@pytest.mark.parametrize("advertised_unit", [None, "GHz"])
def test_provider_acquisition_axis_unit_mismatch_is_rejected_before_run(
    tmp_path: Path,
    advertised_unit: str | None,
) -> None:
    provider = _UnitAbiProvider(
        result_unit="ratio",
        axis_unit=advertised_unit,
        include_axis=True,
    )
    config = load_config()
    environment = build_config_environment(config)
    experiment = load_experiment()
    plan = materialize_local_execution(bind_program_facts(experiment, environment))
    plan = _first_point_plan(plan)
    collect = operations_of_type(plan, CollectOperation, point_index=0)[0]
    request = collect.command.requests[0].model_copy(
        update={
            "dimensions": [
                CollectAxisRequest(id="sample", kind="sample", size=2, unit="ns")
            ]
        }
    )
    updated_collect = replace(
        collect,
        command=collect.command.model_copy(update={"requests": [request]}),
    )
    effects = tuple(
        replace(effect, operation=updated_collect)
        if effect.operation is collect
        else effect
        for effect in plan.effects
    )
    plan = replace(plan, effects=effects)

    with pytest.raises(ProviderContractError) as captured:
        _lower_test_host_binding(plan, config, provider)

    problem = captured.value.problems[0]
    assert len(captured.value.problems) == 1
    assert problem.code == "instrument_acquisition_result_axis_unit_mismatch"
    assert problem.location == model_location(
        "execution_program",
        "operations",
        updated_collect.operation_id,
        "requests",
        "signal",
        "axes",
        0,
        "unit",
    )
    assert not provider.connect_called
    assert sqlite_run_repository(tmp_path).list_runs() == []


def _first_point_plan(
    plan: LocalEffectInspection,
) -> LocalEffectInspection:
    return replace(
        plan,
        points=plan.points[:1],
        effects=tuple(effect for effect in plan.effects if effect.point_index == 0),
    )


def test_provider_description_interruption_precedes_run_acceptance(
    tmp_path: Path,
) -> None:
    provider = _FailingDescriptionProvider(KeyboardInterrupt("description cancelled"))
    config = load_config()
    plan = materialize_local_execution(
        bind_program_facts(load_experiment(), build_config_environment(config))
    )

    with pytest.raises(KeyboardInterrupt, match="description cancelled"):
        _lower_test_host_binding(plan, config, provider)

    assert not provider.connect_called
    assert sqlite_run_repository(tmp_path).list_runs() == []


def test_run_evaluates_residual_compute_per_point(tmp_path: Path) -> None:
    calls: list[Quantity] = []

    def build_program(*, value: object) -> dict[str, object]:
        assert isinstance(value, Quantity)
        calls.append(value)
        return {"value": value}

    operation_id = OperationId(SymbolId(local_id="build-program"))
    result_id = operation_result_id(operation_id)
    slow_axis_type = TableType(
        columns=(TableColumn("frequency", Scalar(QuantityType())),),
    )
    fast_axis_type = TableType(
        columns=(TableColumn("amplitude", Scalar(String())),),
    )
    point_type = TableType(
        columns=(*slow_axis_type.columns, *fast_axis_type.columns),
    )
    product = observable_product("signal", unit="ratio")
    acquisition = instrument_acquisition(
        product,
        resource_port_id="source",
        interface="test.scalar_signal/v1",
    )
    product_use, record_use = record_product(product)
    spec = program_fixture(
        point_domain=PointDomain(
            axes=(
                point_axis_values(
                    "frequency",
                    slow_axis_type.columns[0].value_type,
                    (
                        Quantity(value=4.9, unit="GHz"),
                        Quantity(value=5.1, unit="GHz"),
                    ),
                ),
                point_axis_values(
                    "amplitude",
                    fast_axis_type.columns[0].value_type,
                    ("low", "medium", "high"),
                ),
            ),
        ),
        resource_requirements=(
            LogicalResourceRequirement(
                port_id=logical_resource_port_id("source"),
                capabilities=(
                    InterfaceRef("test.play_program/v1"),
                    InterfaceRef("test.scalar_signal/v1"),
                ),
            ),
        ),
        invocations=[
            instrument_invocation(
                id="play-program",
                resource_port_id=logical_resource_port_id("source"),
                interface="test.play_program/v1",
                operation="play",
                arguments={
                    "program": compute_result(
                        "build-program",
                        value_type=Scalar(Payload("pulse_program")),
                    )
                },
            )
        ],
        product_defs=[product],
        instrument_acquisitions=[acquisition],
        product_uses=[product_use],
        record_uses=[record_use],
        compute_nodes=[
            ComputeNodeFixture(
                id=operation_id,
                implementation=LocalPythonImplementation(
                    id=ImplementationId("python.build-program.v1"),
                    kernel=build_program,
                ),
                result=ComputeOutput(
                    id=result_id,
                    value_type=Scalar(Payload("pulse_program")),
                ),
                input_types={"value": Scalar(QuantityType())},
                inputs={
                    "value": verified_scalar_expr(
                        point_col("frequency", Scalar(QuantityType())),
                        expected_type=Scalar(QuantityType()),
                        bindings=ExpressionTypeBindings(
                            point_row=RowType.from_table(point_type)
                        ),
                    )
                },
            )
        ],
    )
    config = config_with_physical_resources(
        {"source-0": ("test.play_program/v1", "test.scalar_signal/v1")}
    )
    instrument = SignalInstrumentDriver()
    payload_codecs = json_payload_codecs("pulse_program")
    manifest = execute_bound_run(
        config=config,
        experiment=spec,
        instruments=[instrument],
        project_root=tmp_path,
        payload_codecs=payload_codecs,
    )

    assert manifest.status == "completed"
    assert calls == [
        Quantity(value=4.9, unit="GHz"),
        Quantity(value=4.9, unit="GHz"),
        Quantity(value=4.9, unit="GHz"),
        Quantity(value=5.1, unit="GHz"),
        Quantity(value=5.1, unit="GHz"),
        Quantity(value=5.1, unit="GHz"),
    ]
    assert len(instrument.invoked) == 6
    arguments: list[DriverPayload] = []
    for command in instrument.invoked:
        [argument] = command.arguments.values()
        assert isinstance(argument, DriverPayload)
        arguments.append(argument)
    assert [argument.schema_id for argument in arguments] == ["pulse_program"] * 6
    assert [argument.value for argument in arguments] == [
        {"value": {"value": 4.9, "unit": "GHz"}},
        {"value": {"value": 4.9, "unit": "GHz"}},
        {"value": {"value": 4.9, "unit": "GHz"}},
        {"value": {"value": 5.1, "unit": "GHz"}},
        {"value": {"value": 5.1, "unit": "GHz"}},
        {"value": {"value": 5.1, "unit": "GHz"}},
    ]


def test_downstream_compute_receives_result_in_its_declared_type(
    tmp_path: Path,
) -> None:
    received: list[Quantity] = []

    def produce_frequency() -> Quantity:
        return Quantity(value=5_000.0, unit="MHz")

    def consume_frequency(*, frequency: Quantity) -> dict[str, object]:
        received.append(frequency)
        return {"frequency": frequency}

    play_interface = InterfaceRef("test.play_program/v1")
    play = play_interface.operation("play")
    program_argument = play.argument("program")

    @sc.module(id="test.compute-result-type.consumer")
    def consumer(
        context: sc.ModuleContext,
        frequency: Annotated[
            sc.Input[Quantity],
            sc.QuantityType(unit="GHz"),
        ],
    ) -> None:
        program = context.compute(
            "consume-frequency",
            fn=consume_frequency,
            inputs={"frequency": frequency},
            output_type=sc.ScalarType(sc.PayloadType("pulse_program")),
        )
        source = context._resource("source", requires=(play_interface,))
        context._invoke(
            "play-program",
            resource=source,
            operation=play,
            arguments={program_argument: program},
        )

    @sc.module(id="test.compute-result-type.root")
    def root(context: sc.ModuleContext) -> None:
        frequency = context.compute(
            "produce-frequency",
            fn=produce_frequency,
            output_type=sc.ScalarType(sc.QuantityType(dimension="frequency")),
        )
        context.use(consumer.instantiate("consumer", frequency=frequency))

    @sc.experiment(id="test.compute-result-type", kind="compute-result-type")
    def experiment(experiment: sc.ExperimentContext) -> None:
        experiment.use(root())

    config = config_with_physical_resources(
        {"source-0": (play_interface.interface_id,)}
    )
    bound = bind_invocation(experiment(), config_profile=config)
    manifest = execute_bound_run(
        config=config,
        experiment=ProgramFixture(logical=bound.program, bindings=bound.bindings),
        instruments=[SignalInstrumentDriver()],
        project_root=tmp_path,
        payload_codecs=json_payload_codecs("pulse_program"),
    )

    assert manifest.status == "completed"
    assert received == [Quantity(value=5.0, unit="GHz")]


def test_run_skips_unchanged_state_properties(tmp_path: Path) -> None:
    instrument = TestSignalInstrument()
    base_experiment = load_experiment()
    base_bindings = base_experiment.bindings
    experiment = program_fixture(
        point_domain=base_bindings.point_domain,
        resource_requirements=base_bindings.resource_requirements,
        parameter_overlays=base_bindings.parameter_overlays,
        state=(
            state_property(
                base_bindings.resource_requirements[0].port_id,
                interface_id="test.set_frequency/v1",
                property_id="frequency",
                value=verified_scalar_expr(
                    lit(Quantity(value=5.9, unit="GHz")),
                    expected_type=Scalar(QuantityType(unit="GHz")),
                ),
            ),
        ),
        measurement_computes=base_bindings.measurement_computes,
        product_defs=base_bindings.product_defs,
        instrument_acquisitions=base_experiment.logical.program.acquisitions,
        product_uses=base_bindings.product_uses,
        record_uses=base_bindings.record_uses,
    )
    manifest = execute_bound_run(
        config=load_config(),
        experiment=experiment,
        instruments=[instrument],
        project_root=tmp_path,
    )

    assert manifest.status == "completed"
    assert len(instrument.applied_requests) == 1
