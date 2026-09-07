"""Hardware-free connection-residency qualification using public SDK contracts.

Adapters may reuse the probe and contract checker around their own target and
fake native client. The supplied driver and target are a working reference.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Literal, cast
from uuid import uuid4

import scopecat as sc
from scopecat.kernel.errors import OperationFailure, RunFailed, RunIndeterminate
from scopecat.kernel.state import StateValue
from scopecat.kernel.value_types import Scalar, String
from scopecat.program.domain import domain_program
from scopecat.program.products import ModuleProductDecl, ProductValueSpec
from scopecat.records.config import ConfigProfileSnapshot, DomainTargetBinding
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.run import RunSnapshot
from scopecat.sdk.domain import (
    DomainBatchCandidate,
    DomainBatchPreparationCost,
    DomainBatchPreparationLimits,
    DomainBatchRequest,
    DomainExecutionReceipt,
    DomainExecutionResult,
    DomainInstrumentExecutor,
    DomainInvocationSpec,
    DomainPreparationBuilder,
    DomainResidencyAddress,
    DomainResidencyRequirement,
    DomainResultBinding,
    DomainResultValue,
    PreparedDomainExecution,
)
from scopecat.sdk.instruments import (
    DriverAcquisition,
    DriverCatalog,
    DriverOperation,
    DriverOutcome,
    DriverReadback,
    DriverRejected,
    DriverStatePatch,
    DriverStateReadback,
    DriverStateReadRequest,
    DriverSuccess,
    DriverUnknown,
    InstrumentBackend,
    InstrumentConnectionContext,
    InstrumentDescription,
    InstrumentProviderContext,
    InstrumentProviderDescription,
    OperationCostMeasurement,
    acquisition,
    acquisition_result,
    interface,
    operation,
    operation_argument,
    state_readback,
)
from scopecat.sdk.instruments.commands import (
    CollectResultRequest,
    InstrumentOperationArgument,
)
from scopecat.sdk.instruments.execution import (
    RunHardwareBatch,
    RunHardwareCollect,
    RunHardwareCollectBinding,
    RunHardwareInvoke,
)
from scopecat.sdk.problems import ProblemPhase, problem

from scopecat_testkit.domain import domain_call
from scopecat_testkit.instrument_drivers import load_config

_INTERFACE = "testkit.volatile_program/v1"
_INSTRUMENT = "source-0"


@dataclass(frozen=True)
class ResidencyEvent:
    connection: str
    operation: str
    content: str | None


@dataclass(frozen=True)
class ResidencyProbe:
    """Small file-backed probe that survives replacement of a worker process."""

    root: Path

    def record(self, connection: str, operation: str, content: str | None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with (self.root / "residency-events.jsonl").open("a", encoding="utf-8") as log:
            log.write(json.dumps([connection, operation, content]) + "\n")

    def events(self) -> tuple[ResidencyEvent, ...]:
        path = self.root / "residency-events.jsonl"
        if not path.exists():
            return ()
        return tuple(
            ResidencyEvent(*json.loads(line)) for line in path.read_text().splitlines()
        )

    def counts(self) -> tuple[int, int, int]:
        events = self.events()
        setup, trigger, collect = (
            sum(item.operation == operation for item in events)
            for operation in ("setup", "trigger", "collect")
        )
        return setup, trigger, collect

    def inject(
        self, fault: Literal["setup_rejected", "trigger_unknown", "collect_unknown"]
    ) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / fault).touch()

    def consume(
        self, fault: Literal["setup_rejected", "trigger_unknown", "collect_unknown"]
    ) -> bool:
        path = self.root / fault
        if not path.exists():
            return False
        path.unlink()
        return True


class VolatileProgramDriver:
    """A fresh connection has no program, even when durable setup once succeeded."""

    implementation_id = "testkit.volatile_program"
    implementation_version = "1"

    def __init__(self, probe: ResidencyProbe, instrument_id: str = _INSTRUMENT) -> None:
        self.probe = probe
        self.instrument_id = instrument_id
        self.connection = uuid4().hex
        self.loaded: str | None = None
        self.device_bytes = b""
        self.triggered = False
        self.probe.record(self.connection, "connect", None)

    def describe(self) -> InstrumentDescription:
        return volatile_description(self.instrument_id)

    def read_state(self, request: DriverStateReadRequest) -> DriverStateReadback:
        return state_readback(request, {})

    def apply_state(
        self, request: DriverStatePatch
    ) -> DriverOutcome[DriverStateReadback | None]:
        assert not request.entries
        return DriverSuccess(None)

    def invoke(
        self, request: DriverOperation
    ) -> DriverOutcome[DriverStateReadback | None]:
        if request.target.operation_id == "load":
            self.probe.record(self.connection, "setup", self.loaded)
            if self.probe.consume("setup_rejected"):
                self.loaded = None
                return DriverRejected(
                    (
                        problem(
                            "fixture_setup_rejected",
                            "virtual setup rejected before loading",
                            phase=ProblemPhase.EXECUTION,
                        ),
                    )
                )
            started = perf_counter()
            self.loaded = cast("str", request.arguments["content"])
            self.device_bytes = self.loaded.encode("utf-8")
            elapsed = perf_counter() - started
            return DriverSuccess(
                None,
                measured_cost=OperationCostMeasurement(
                    source="testkit_virtual_program_buffer",
                    transfer_seconds=elapsed,
                    uploaded_bytes=len(self.device_bytes),
                    reused_bytes=0,
                    retained_bytes=len(self.device_bytes),
                    unavailable_reason=(
                        "virtual UTF-8 buffer assignment; "
                        "no waveform rendering or physical bus"
                    ),
                ),
            )
        assert request.target.operation_id == "trigger"
        assert self.loaded is not None, (
            "trigger reached a connection without loaded content"
        )
        self.triggered = True
        self.probe.record(self.connection, "trigger", self.loaded)
        if self.probe.consume("trigger_unknown"):
            return DriverUnknown(
                (
                    problem(
                        "fixture_trigger_response_lost",
                        "virtual trigger executed but its response was lost",
                        phase=ProblemPhase.EXECUTION,
                    ),
                )
            )
        return DriverSuccess(
            None,
            measured_cost=OperationCostMeasurement(
                source="testkit_virtual_program_buffer",
                uploaded_bytes=0,
                reused_bytes=len(self.device_bytes),
                retained_bytes=len(self.device_bytes),
                unavailable_reason=(
                    "loaded program use; "
                    "transfer/acquisition interval not measured here"
                ),
            ),
        )

    def collect(self, request: DriverAcquisition) -> DriverOutcome[DriverReadback]:
        assert self.loaded is not None and self.triggered
        self.probe.record(self.connection, "collect", self.loaded)
        self.triggered = False
        started = perf_counter()
        readback = DriverReadback(
            values={
                result: MeasurementScalar.create(
                    dtype="float64", value=1.0, unit="count"
                )
                for result in request.results
            }
        )

        if self.probe.consume("collect_unknown"):
            return DriverUnknown(
                (
                    problem(
                        "fixture_collect_response_lost",
                        "virtual collect executed but its response was lost",
                        phase=ProblemPhase.EXECUTION,
                    ),
                )
            )
        return DriverSuccess(
            readback,
            measured_cost=OperationCostMeasurement(
                source="testkit_virtual_scalar_acquisition",
                acquire_seconds=perf_counter() - started,
                uploaded_bytes=0,
                reused_bytes=0,
                retained_bytes=len(self.device_bytes),
                unavailable_reason=(
                    "virtual scalar construction; no physical bus or waveform rendering"
                ),
            ),
        )

    def abort(self) -> None:
        self.triggered = False

    def disconnect(self) -> None:
        self.loaded = None
        self.device_bytes = b""
        self.triggered = False
        self.probe.record(self.connection, "disconnect", None)


def volatile_description(instrument_id: str = _INSTRUMENT) -> InstrumentDescription:
    """Pure discovery must not open a connection or populate device state."""
    return InstrumentDescription(
        instrument_id=instrument_id,
        implementation_id=VolatileProgramDriver.implementation_id,
        implementation_version=VolatileProgramDriver.implementation_version,
        interfaces=[
            interface(
                _INTERFACE,
                operations=[
                    operation(
                        "load",
                        arguments=[
                            operation_argument("content", value_type=Scalar(String()))
                        ],
                    ),
                    operation("trigger"),
                ],
                acquisitions=[
                    acquisition(
                        "sample", results=[acquisition_result("signal", unit="count")]
                    )
                ],
            )
        ],
    )


@dataclass(frozen=True)
class VolatileProgramProvider:
    probe: ResidencyProbe
    provider_id: str = "testkit.volatile_program"

    def describe(
        self, context: InstrumentProviderContext
    ) -> InstrumentProviderDescription:
        return InstrumentProviderDescription(
            provider_id=self.provider_id,
            instruments=tuple(
                volatile_description(binding.id) for binding in context.bindings
            ),
        )

    def connect(self, context: InstrumentConnectionContext) -> VolatileProgramDriver:
        return VolatileProgramDriver(self.probe, context.binding.id)


def volatile_backend(root: Path) -> InstrumentBackend:
    provider = VolatileProgramProvider(ResidencyProbe(root))
    return InstrumentBackend(
        provider=provider,
        driver_catalog=DriverCatalog(provider_id=provider.provider_id),
    )


@dataclass(frozen=True)
class VolatileProgramTarget:
    """One-point target batches share device content, never trigger identities."""

    content: str = "equal-device-content"
    before_trigger: Callable[[], None] | None = field(
        default=None, repr=False, compare=False
    )
    target_id: str = "testkit.volatile_program"
    target_kind: str = "testkit.volatile_program"
    instrument_ids: tuple[str, ...] = (_INSTRUMENT,)

    def initial_batch_preparation_limits(
        self, point_count: int
    ) -> DomainBatchPreparationLimits:
        del point_count
        return DomainBatchPreparationLimits(
            max_points=1, max_retained_bytes=len(self.content)
        )

    def prepare_batch(self, request: DomainBatchRequest) -> DomainBatchCandidate:
        del request
        return DomainBatchCandidate(
            compatible_point_count=1,
            preparation_cost=DomainBatchPreparationCost(
                analyzed_point_count=1, retained_bytes=len(self.content)
            ),
            _compile=self.compile,
        )

    def compile(self, request: DomainBatchRequest) -> PreparedDomainExecution:
        preparation = DomainPreparationBuilder(request)
        [point] = request.points
        [product] = request.product_uses
        mapping = preparation.map_measurements(
            results=(DomainResultBinding("signal", point, product),)
        )
        fingerprint = hashlib.sha256(self.content.encode()).hexdigest()
        return preparation.build(
            instrument_ids=self.instrument_ids,
            setup=self,
            setup_residency_requirements=(
                DomainResidencyRequirement(
                    DomainResidencyAddress(_INSTRUMENT, "program"), fingerprint
                ),
            ),
            state_requirements=(),
            realtime_write_footprint=(),
            realtime_state_invalidations=(),
            next_batch_max_points=1,
            mapping=mapping,
            invocation=DomainInvocationSpec(
                invocation_id=f"program-{point.ordinal}",
                target_id=self.target_id,
                compiler_id="testkit.volatile_program",
                capability_fingerprint="v1",
                artifact_id="program",
                artifact_fingerprint=fingerprint,
                execution_summary={},
                target_intent={},
                payload=self.content,
            ),
            job_runtime=self,
            realize=self.realize,
        )

    def prepare(
        self,
        execution_key: str,
        payload: str,
        /,
        *,
        instruments: DomainInstrumentExecutor,
    ) -> None:
        receipt = instruments.execute(
            RunHardwareBatch(
                operation_id=f"{execution_key}:setup",
                actions=(
                    RunHardwareInvoke(
                        effect_id=f"{execution_key}:load",
                        instrument_id=_INSTRUMENT,
                        resource_id=_INSTRUMENT,
                        interface_id=_INTERFACE,
                        operation_id="load",
                        arguments=(
                            InstrumentOperationArgument(
                                id="content", value=StateValue(payload)
                            ),
                        ),
                    ),
                ),
            )
        )
        if receipt.problems:
            raise OperationFailure(receipt.problems)

    def start(
        self,
        execution_key: str,
        payload: str,
        /,
        *,
        instruments: DomainInstrumentExecutor,
    ) -> DomainExecutionResult[MeasurementScalar] | DomainExecutionReceipt:
        del payload
        if self.before_trigger is not None:
            self.before_trigger()
        receipt = instruments.execute(
            RunHardwareBatch(
                operation_id=f"{execution_key}:acquire",
                actions=(
                    RunHardwareInvoke(
                        effect_id=f"{execution_key}:trigger",
                        instrument_id=_INSTRUMENT,
                        resource_id=_INSTRUMENT,
                        interface_id=_INTERFACE,
                        operation_id="trigger",
                    ),
                    RunHardwareCollect(
                        effect_id=f"{execution_key}:collect",
                        instrument_id=_INSTRUMENT,
                        point_count=1,
                        requests=(
                            CollectResultRequest(
                                id="signal",
                                interface_id=_INTERFACE,
                                acquisition_id="sample",
                                result_id="signal",
                                unit="count",
                            ),
                        ),
                        bindings=(
                            RunHardwareCollectBinding(
                                request_id="signal", value_ids=("signal",)
                            ),
                        ),
                    ),
                ),
            )
        )
        if receipt.problems or receipt.indeterminate:
            return DomainExecutionReceipt(
                execution_key=execution_key,
                status="unknown" if receipt.indeterminate else "not_executed",
                problems=receipt.problems,
            )
        [value] = receipt.values
        assert isinstance(value.value, MeasurementScalar)
        return DomainExecutionResult(
            DomainExecutionReceipt(
                execution_key=execution_key,
                status="completed",
                result_fingerprint="signal-1",
                result_count=1,
            ),
            value.value,
        )

    def realize(
        self, result: DomainExecutionResult[MeasurementScalar]
    ) -> Iterable[DomainResultValue[str]]:
        return (DomainResultValue("signal", result.result),)


def residency_config() -> ConfigProfileSnapshot:
    config = load_config()
    instrument = config.instrument_registry.instruments[0].model_copy(
        update={
            "default_state": [],
            "run_start": "preserve",
            "success_action": "release",
            "safe_state": [],
            "failure_action": "abort_and_release",
        }
    )
    return config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "instrument_registry": config.instrument_registry.model_copy(
                        update={"instruments": [instrument]}
                    ),
                    "domain_target": DomainTargetBinding(
                        id="testkit.volatile_program",
                        kind="testkit.volatile_program",
                        instrument_ids=[_INSTRUMENT],
                    ),
                }
            )
        }
    )


def residency_experiment(points: int = 2) -> sc.ExperimentInvocation:
    coordinate = sc.coordinate("repetition", sc.IntType())
    program = domain_program(
        "volatile-program",
        dialect_id="testkit",
        dialect_version="1",
        body="equal-device-content",
        results={"signal": ("signal", "v1")},
    )
    call = domain_call(
        program,
        products={
            "signal": ModuleProductDecl(
                "signal", value_spec=ProductValueSpec(unit="count", dtype="float64")
            )
        },
    )

    @sc.experiment(id="testkit.connection_residency")
    def experiment(context: sc.ExperimentContext) -> None:
        context.grid(sc.axis(coordinate, tuple(range(points))))
        result = context.use(call)
        context.alias(result.signal, record_id="signal")

    return experiment()


def check_connection_residency(
    run: Callable[[int], RunSnapshot],
    probe: ResidencyProbe,
    *,
    assert_unknown: Callable[[Callable[[], RunSnapshot]], None] | None = None,
) -> None:
    """Qualify an adapter against one fresh probe and independently admitted runs.

    ``run(points)`` executes equal device content in one-point target batches and
    returns terminal evidence or raises the public RunFailed/RunIndeterminate
    errors. Use the probe in the adapter's fake native client;
    a new connection must start empty and honor the two named one-shot faults.
    This check requires run-local setup but does not prescribe physical connection
    reuse across runs; release/reconnect is qualified separately under its policy.
    ``assert_unknown`` may execute the supplied zero-argument run and assert its
    actual public error and durable attention evidence when the execution surface
    fences an uncertain run instead of raising RunIndeterminate.
    """
    assert probe.events() == ()
    assert run(2).status == "completed"
    assert probe.counts() == (1, 2, 2)
    triggers = [event for event in probe.events() if event.operation == "trigger"]
    assert triggers[0].connection == triggers[1].connection
    assert triggers[0].content is not None
    assert triggers[0].content == triggers[1].content
    assert run(1).status == "completed"
    assert probe.counts() == (2, 3, 3)
    probe.inject("setup_rejected")
    try:
        run(1)
    except RunFailed as failed:
        failed_outcome = failed.outcome
    else:
        raise AssertionError("rejected setup must fail the run before triggering")
    assert failed_outcome.certainty == "known"
    assert probe.counts() == (3, 3, 3)
    assert run(1).status == "completed"
    assert probe.counts() == (4, 4, 4)
    probe.inject("trigger_unknown")
    if assert_unknown is None:
        assert_unknown = _assert_indeterminate
    assert_unknown(lambda: run(2))
    assert probe.counts() == (5, 5, 4)


def _assert_indeterminate(run: Callable[[], RunSnapshot]) -> None:
    try:
        run()
    except RunIndeterminate as unknown:
        outcome = unknown.outcome
    else:
        raise AssertionError("lost trigger response must remain indeterminate")
    assert outcome.certainty == "indeterminate"
