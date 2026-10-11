"""Bounded device evidence for host bias and point-local quantum routing."""

from __future__ import annotations

from typing import cast

import scopecat as sc
from scopecat.compiler.bind import bind_program
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.config.environment import build_config_environment
from scopecat.execution.local.program import ApplyStateOperation, InvokeOperation
from scopecat.execution.program import RunCoverageEffect, RunDomainJob
from scopecat.kernel.entity import EntityRef
from scopecat.planning.compilation import compile_run_program
from scopecat.planning.provider_binding import resolve_instrument_contract_catalog
from scopecat.records.execution import InstrumentStateEvidence
from scopecat.records.measurement import MeasurementScalar
from scopecat.records.parameter_revision import ParameterRevision
from scopecat.records.run import ParameterRunConfigSource
from scopecat.sdk.domain import DomainExecutionEvidence
from scopecat_instruments import DCSourceTarget, dc_source
from scopecat_testkit.instrument_host import compose_test_instruments

from reference_lab.application import create_application
from reference_lab.compiler import QuantumLabCompiler
from reference_lab.configuration import EXAMPLE_ROOT, bootstrap_config
from reference_lab.payloads import reference_lab_payload_codecs
from reference_lab.provider import ReferenceLabProvider
from reference_lab.quantum_runner import quantum_capture
from reference_lab.targets.list_mode import (
    MappedListModeTarget,
    configured_list_mode_target,
)
from reference_lab.workflows.ramsey import ramsey_program


@sc.experiment
def _biased_entity_capture(experiment: sc.ExperimentContext) -> None:
    bias = experiment.scan("bias", (sc.Quantity(-0.1, "V"), sc.Quantity(0.1, "V")))
    qubit = experiment.scan(
        "qubit", tuple(EntityRef(id=id_, kind="logical_qubit") for id_ in ("q1", "q0"))
    )
    delay = experiment.scan("delay", (sc.Quantity(8, "ns"), sc.Quantity(48, "ns")))
    source = dc_source(
        experiment, for_=sc.one(EntityRef(id="q0", kind="logical_qubit"))
    )
    source.ensure(current_protection=sc.Quantity(100, "uA"), output_enabled=False)
    source.source_voltage(range=sc.Quantity(1, "V"), level=bias)
    source.ensure(output_enabled=True)
    probabilities = experiment.use(
        quantum_capture(
            ramsey_program(
                qubit=qubit, delay=delay, phase=sc.Quantity(0, "rad")
            ).with_shots(64)
        )
    )
    experiment.alias(probabilities.probability_1, record_id="probability")
    experiment.on_success(source, DCSourceTarget(output_enabled=False))


def test_bias_effects_and_point_local_quantum_routes_compile_together() -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider(seed=7)
    catalog = resolve_instrument_contract_catalog(
        config=config, provider_id=provider.provider_id, describe=provider.describe
    )
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        domain_compiler=QuantumLabCompiler(
            target=configured_list_mode_target(config, catalog)
        ),
        payload_codecs=reference_lab_payload_codecs(),
    )
    plan = compile_run_program(
        composition.system,
        bound=bind_program(
            compile_invocation(_biased_entity_capture.build()).program,
            build_config_environment(config),
        ),
    )
    coverage = tuple(plan.coverage)
    jobs = [item for item in coverage if isinstance(item, RunDomainJob)]
    assert [job.point_ordinals for job in jobs] == [(point,) for point in range(8)]
    drive_channels: dict[str, set[str]] = {}
    for point, job in enumerate(jobs):
        expected_entity = ("q1", "q0")[(point // 2) % 2]
        mapped = cast("MappedListModeTarget", job.execution.invocation.payload)
        placement = mapped.artifact.placement
        assert placement.logical_qubit_ids == (expected_entity,)
        assert {event.signal.signal[-1] for event in placement.events} == {
            expected_entity
        }
        drive_channels[expected_entity] = {
            endpoint.channel_id
            for event in placement.events
            if event.signal.signal[0] == "drive"
            for endpoint in event.signal.endpoints
        }
        # Host writes must finish before the corresponding target acquisition.
        preceding = coverage[: coverage.index(job)]
        effects = [
            item.operation
            for item in preceding
            if isinstance(item, RunCoverageEffect)
            and item.point_index == point
            and isinstance(item.operation, (ApplyStateOperation, InvokeOperation))
            and item.operation.instrument_id == "flux-dac-a"
        ]
        disabled, voltage, enabled = effects
        assert isinstance(disabled, ApplyStateOperation)
        assert any(
            target.property_id == "output_enabled" and target.value.root is False
            for target in disabled.targets
        )
        assert isinstance(voltage, InvokeOperation)
        assert voltage.operation_id == "source_voltage"
        assert voltage.entity_ids == ("q0",)
        assert {argument.id: argument.value.root for argument in voltage.arguments} == {
            "range": sc.Quantity(1, "V"),
            "level": sc.Quantity(-0.1 if point < 4 else 0.1, "V"),
        }
        assert isinstance(enabled, ApplyStateOperation)
        assert any(
            target.property_id == "output_enabled" and target.value.root is True
            for target in enabled.targets
        )
    assert len(drive_channels["q0"]) == len(drive_channels["q1"]) == 2
    assert drive_channels["q0"].isdisjoint(drive_channels["q1"])
    assert {requirement.id for requirement in plan.resource_requirements} >= {
        "flux-dac-a",
        "drive-awg",
        "readout-awg",
        "readout-digitizer",
        "timing-controller",
    }


def test_bias_and_entity_capture_worker_retains_values_and_inputs(
    independent_lab_daemon: str,
    independent_parameters: ParameterRevision,
) -> None:
    with create_application(EXAMPLE_ROOT).connect(independent_lab_daemon) as lab:
        setup = lab.setup.get("initial")
        inputs = lab.parameters.resolve(independent_parameters, setup=setup)
        run = lab.prepare(_biased_entity_capture.build(), config=inputs).run()
        assert run.status == "completed"
        source = run.snapshot.config_source
        assert isinstance(source, ParameterRunConfigSource)
        assert source.parameters == independent_parameters.ref
        assert source.setup == setup.ref
        data = run.measurements()
        assert len(data) == 8
        assert data["probability"].definition.unit == "ratio"
        for point, record in enumerate(data.records):
            assert record.coordinates["bias"] == MeasurementScalar(
                kind="scalar",
                dtype="float64",
                unit="V",
                value=-0.1 if point < 4 else 0.1,
            )
            qubit = record.coordinates["qubit"]
            assert isinstance(qubit, MeasurementScalar)
            assert qubit.value == ("q1", "q0")[(point // 2) % 2]
            assert record.coordinates["delay"] == MeasurementScalar(
                kind="scalar", dtype="float64", unit="ns", value=(8.0, 48.0)[point % 2]
            )
            # Existing Ramsey simulator: rounded 64-shot counts at 8 and 48 ns.
            # Its response is bias-independent; this does not claim flux physics.
            assert record.observables["probability"] == MeasurementScalar(
                kind="scalar",
                dtype="float64",
                unit="ratio",
                value=(55 / 64, 11 / 64)[point % 2],
            )
        domain = DomainExecutionEvidence.model_validate(
            run.record_json("domain-execution-evidence").content
        )
        assert domain.run_id == run.id and domain.detail_complete
        assert (
            domain.completed_count == domain.receipt_count == domain.attempt_count == 8
        )
        state = InstrumentStateEvidence.model_validate(
            run.record_json("instrument-state-evidence").content
        )
        [dc] = [
            item for item in state.final_state if item.instrument_id == "flux-dac-a"
        ]
        observed = {
            observation.target.property_id: observation.value.root
            for observation in dc.observations
            if observation.entity_ids == ("q0",)
        }
        assert observed["actual_voltage"] == sc.Quantity(0.1, "V")
        assert observed["target_voltage"] == sc.Quantity(0.1, "V")
        assert observed["output_enabled"] is False

        assert lab.parameters.get(independent_parameters.id) == independent_parameters
        assert lab.setup.get("initial") == setup
        assert lab.config.registry().entries == ()
