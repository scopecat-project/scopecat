"""Software provenance participates in the ordinary executable setup contract."""

import pytest
from pydantic import ValidationError
from scopecat_testkit.config_registry import load_config

from scopecat.config.scientific_binding import bind_scientific_evidence
from scopecat.records.config import ConfigProfileSnapshot, SystemSpec
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.scientific_binding import ResolvedScientificBinding
from scopecat.records.setup import ExecutableSetupSnapshot


def scenario(seed: int = 1) -> SoftwareExecutionScenario:
    return SoftwareExecutionScenario(
        id="test-scene",
        label="Test scene",
        model_id="test-model",
        model_version="1",
        seed=seed,
        settings={"noise": 0.1},
        capabilities=("protocol",),
        limitations=("No sample physics",),
    )


def test_scenario_is_retained_and_changes_executable_identity() -> None:
    config = load_config()
    plain = ExecutableSetupSnapshot.from_config(config)
    payload = plain.model_dump()
    payload["scenario"] = scenario().model_dump()
    setup = ExecutableSetupSnapshot.model_validate(payload)
    composed = setup.compose(config)
    assert composed.system.scenario == scenario()
    assert ExecutableSetupSnapshot.from_config(composed) == setup
    assert setup.execution_content_hash != plain.execution_content_hash
    assert setup.content_hash != plain.content_hash
    changed = setup.model_copy(update={"scenario": scenario(2)})
    assert changed.execution_content_hash != setup.execution_content_hash
    assert (
        ConfigProfileSnapshot.model_validate_json(composed.model_dump_json())
        == composed
    )
    binding = bind_scientific_evidence(
        catalog_id="test",
        config=composed,
        samples=(),
        sample_revisions={},
    )
    assert binding.scenario == scenario()
    assert (
        ResolvedScientificBinding.model_validate_json(binding.model_dump_json())
        == binding
    )


@pytest.mark.parametrize(
    "connection",
    [
        {"kind": "tcpip_socket", "host": "localhost", "port": 5000},
        {"kind": "serial", "port": "COM1"},
        {"kind": "driver_managed"},
    ],
)
def test_software_scene_rejects_physical_connections_outside_domain_target(
    connection: dict[str, object],
) -> None:
    config = load_config()
    payload = config.system.model_dump()
    payload["scenario"] = scenario().model_dump()
    payload["domain_target"] = None
    payload["instrument_registry"]["instruments"][0]["connection"] = connection
    with pytest.raises(ValidationError, match="software scenario requires virtual"):
        SystemSpec.model_validate(payload)
    payload.pop("id")
    payload.pop("parameter_catalog")
    with pytest.raises(ValidationError, match="software scenario requires virtual"):
        ExecutableSetupSnapshot.model_validate(payload)
