from __future__ import annotations

from scopecat.compiler.bind import bind_program
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.config.environment import build_config_environment
from scopecat.execution.local.program import (
    ApplyStateOperation,
    CollectOperation,
    InvokeOperation,
)
from scopecat.execution.program import RunCoverageEffect
from scopecat.kernel.resource_identity import ResourceRequirement
from scopecat.planning.compilation import compile_run_program
from scopecat.planning.provider_binding import resolve_instrument_contract_catalog
from scopecat.program.products import RecordSelection
from scopecat.sdk.instruments import InterfaceRef
from scopecat_testkit.instrument_host import compose_test_instruments

from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT,
    AWG_SEQUENCER,
    OSCILLOSCOPE_CONTROL,
    OSCILLOSCOPE_INPUT,
)
from reference_lab.compiler import QuantumLabCompiler
from reference_lab.configuration import bootstrap_config
from reference_lab.payloads import reference_lab_payload_codecs
from reference_lab.provider import ReferenceLabProvider
from reference_lab.targets.list_mode import configured_list_mode_target
from reference_lab.workflows.awg_output_monitor import AWG_OUTPUT_MONITOR
from reference_lab.workflows.drag_beta_experiment import drag_beta_experiment


def test_awg_output_monitor_uses_entityless_bench_resources() -> None:
    logical = compile_invocation(AWG_OUTPUT_MONITOR).program.program

    assert [port.id for port in logical.resource_ports] == ["source", "monitor"]
    source, monitor = logical.resource_ports
    assert len(source.selector.capabilities) == 6
    assert len(monitor.selector.capabilities) == 12
    assert all(
        not isinstance(capability, InterfaceRef)
        for port in (source, monitor)
        for capability in port.selector.capabilities
    )
    assert source.selector.interfaces == (
        AWG_SEQUENCER.interface_id,
        ANALOG_WAVEFORM_OUTPUT.interface_id,
    )
    assert monitor.selector.interfaces == (
        OSCILLOSCOPE_CONTROL.interface_id,
        OSCILLOSCOPE_INPUT.interface_id,
    )
    assert source.selector.entity_inputs == ()
    assert monitor.selector.entity_inputs == ()
    selections = logical.product_record_selections
    assert all(isinstance(selection, RecordSelection) for selection in selections)
    assert [
        selection.product_id.qualified_name
        for selection in selections
        if isinstance(selection, RecordSelection)
    ] == ["time", "voltage"]


def test_awg_output_monitor_arms_plays_and_fetches_in_order() -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider()
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        payload_codecs=reference_lab_payload_codecs(),
    )
    compiled = compile_invocation(AWG_OUTPUT_MONITOR)
    bound = bind_program(compiled.program, build_config_environment(config))
    plan = compile_run_program(composition.system, bound=bound)
    operations = [
        effect.operation
        for effect in plan.coverage
        if isinstance(effect, RunCoverageEffect) and effect.point_index == 0
    ]
    hardware = [
        operation
        for operation in operations
        if isinstance(
            operation,
            ApplyStateOperation | InvokeOperation | CollectOperation,
        )
    ]

    assert [type(operation) for operation in hardware] == [
        ApplyStateOperation,
        ApplyStateOperation,
        InvokeOperation,
        InvokeOperation,
        CollectOperation,
    ]
    assert [operation.instrument_id for operation in hardware] == [
        "drive-awg",
        "bench-scope",
        "bench-scope",
        "drive-awg",
        "bench-scope",
    ]

    awg_state = hardware[0]
    scope_state = hardware[1]
    assert isinstance(awg_state, ApplyStateOperation)
    assert isinstance(scope_state, ApplyStateOperation)
    assert {target.component_path for target in awg_state.targets} == {
        (),
        ("outputs", "ch1"),
    }
    assert {target.component_path for target in scope_state.targets} == {
        (),
        ("inputs", "ch1"),
    }

    arm = hardware[2]
    play = hardware[3]
    fetch = hardware[4]
    assert isinstance(arm, InvokeOperation)
    assert isinstance(play, InvokeOperation)
    assert isinstance(fetch, CollectOperation)
    assert arm.operation_id == "arm"
    assert arm.component_path == ()
    assert play.operation_id == "play"
    assert play.component_path == ("outputs", "ch1")
    assert {tuple(request.component_path) for request in fetch.command.requests} == {
        ("inputs", "ch1")
    }
    assert arm.entity_ids == ()
    assert play.entity_ids == ()
    assert all(not request.entity_ids for request in fetch.command.requests)


def test_monitor_and_quantum_target_claim_the_same_physical_awg() -> None:
    config = bootstrap_config()
    provider = ReferenceLabProvider()
    catalog = resolve_instrument_contract_catalog(
        config=config,
        provider_id=provider.provider_id,
        describe=provider.describe,
    )
    composition = compose_test_instruments(
        config=config,
        provider=provider,
        domain_compiler=QuantumLabCompiler(
            target=configured_list_mode_target(config, catalog)
        ),
        payload_codecs=reference_lab_payload_codecs(),
    )

    monitor_compiled = compile_invocation(AWG_OUTPUT_MONITOR)
    monitor_plan = compile_run_program(
        composition.system,
        bound=bind_program(
            monitor_compiled.program,
            build_config_environment(config),
        ),
    )
    quantum_compiled = compile_invocation(drag_beta_experiment.build())
    quantum_plan = compile_run_program(
        composition.system,
        bound=bind_program(
            quantum_compiled.program,
            build_config_environment(config),
        ),
    )
    drive_awg = ResourceRequirement("drive-awg")

    assert drive_awg in monitor_plan.resource_requirements
    assert drive_awg in quantum_plan.resource_requirements
    assert quantum_plan.host is not None
    assert "drive-awg" in quantum_plan.host.resource_order
