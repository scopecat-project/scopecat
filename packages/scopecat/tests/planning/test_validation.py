import pytest
from pydantic import ValidationError
from scopecat_testkit.paths import CORE_FIXTURE_DIR as EXAMPLE_DIR

from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.profile_validation import validate_config_profile
from scopecat.records.config import ConfigProfileSnapshot


def load_config() -> ConfigProfileSnapshot:
    return load_config_snapshot_document(EXAMPLE_DIR / "config-snapshot.json")


def _problem_codes(config: ConfigProfileSnapshot) -> set[str]:
    return {problem.code for problem in validate_config_profile(config)}


def test_valid_example_has_no_problems() -> None:
    config = load_config()

    problems = validate_config_profile(config)

    assert not bool(problems)


def test_domain_target_instruments_must_be_registered() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["domain_target"]["instrument_ids"] = ["missing"]

    with pytest.raises(
        ValidationError,
        match="unknown domain target instrument",
    ):
        ConfigProfileSnapshot.model_validate(config_data)


def test_domain_target_instruments_must_be_unique() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["domain_target"]["instrument_ids"] = [
        "source-0",
        "source-0",
    ]

    with pytest.raises(
        ValidationError,
        match="instrument ids must be unique",
    ):
        ConfigProfileSnapshot.model_validate(config_data)


def test_primary_entity_must_be_declared_in_topology() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["primary_entity_id"] = "missing"
    config = ConfigProfileSnapshot.model_validate(config_data)

    assert "configuration.unknown_primary_entity" in _problem_codes(config)


def test_resource_route_must_reference_registered_instrument() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["routing"]["routes"][0]["instrument_id"] = "missing"
    config = ConfigProfileSnapshot.model_validate(config_data)

    assert "configuration.unknown_resource_route_instrument" in _problem_codes(config)


def test_resource_route_endpoint_must_reference_declared_entity() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["routing"]["routes"][0]["endpoints"][0]["entity_id"] = (
        "missing"
    )
    config_data["system"]["routing"]["routes"][0]["entity_ids"] = ["q0", "missing"]
    config = ConfigProfileSnapshot.model_validate(config_data)

    assert "configuration.unknown_resource_route_entity" in _problem_codes(config)


def test_resource_route_channel_may_describe_shared_infrastructure() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["routing"]["routes"][0]["endpoints"][0]["entity_id"] = None
    config = ConfigProfileSnapshot.model_validate(config_data)

    assert not bool(validate_config_profile(config))


def test_one_endpoint_key_can_map_to_multiple_explicit_channels() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["routing"]["routes"][0]["endpoints"].append(
        {
            "interface_id": "test.set_frequency/v1",
            "entity_id": "q0",
            "channel_id": "readout-q0",
        }
    )
    config = ConfigProfileSnapshot.model_validate(config_data)

    assert not bool(validate_config_profile(config))


def test_unsupported_unit_fails_model_validation() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["parameter_snapshot"]["values"][0]["value"]["unit"] = "furlong"

    with pytest.raises(ValidationError):
        ConfigProfileSnapshot.model_validate(config_data)


def test_parameter_value_without_definition_is_error() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["parameter_catalog"]["definitions"] = []
    config = ConfigProfileSnapshot.model_validate(config_data)

    problems = validate_config_profile(config)

    assert bool(problems)
    assert problems[0].code == "unknown_parameter_definition"


def test_duplicate_parameter_id_fails_model_validation() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["parameter_snapshot"]["values"].append(
        config_data["parameter_snapshot"]["values"][0]
    )

    with pytest.raises(ValidationError):
        ConfigProfileSnapshot.model_validate(config_data)


def test_incompatible_parameter_value_unit_is_error() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["parameter_snapshot"]["values"][0]["value"]["unit"] = "dBm"
    config = ConfigProfileSnapshot.model_validate(config_data)

    problems = validate_config_profile(config)

    assert bool(problems)
    assert problems[0].code == "incompatible_parameter_quantity_unit"


def test_parameter_value_outside_declared_bounds_is_error() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["parameter_snapshot"]["values"][0]["value"]["value"] = 7.0
    config = ConfigProfileSnapshot.model_validate(config_data)

    problems = validate_config_profile(config)

    assert bool(problems)
    assert problems[0].code == "invalid_parameter_quantity"


def test_parameter_table_rows_are_validated_against_catalog() -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["parameter_catalog"]["definitions"].append(
        {
            "id": "readout_steps",
            "value_type": {
                "shape": "table",
                "primary_key": ["step_id"],
                "columns": [
                    {"id": "step_id", "value_type": {"type": "string"}},
                    {
                        "id": "frequency",
                        "value_type": {"type": "quantity", "unit": "GHz"},
                    },
                ],
            },
        }
    )
    config_data["parameter_snapshot"]["values"].append(
        {
            "id": "readout_steps",
            "shape": "table",
            "rows": [
                {
                    "step_id": "prepare",
                    "frequency": {"value": 120.0, "unit": "ns"},
                }
            ],
        }
    )
    config = ConfigProfileSnapshot.model_validate(config_data)

    problems = validate_config_profile(config)

    assert bool(problems)
    assert problems[0].code == "invalid_parameter_value"


@pytest.mark.parametrize("unknown_value", [False, True])
def test_equivalent_quantity_primary_keys_are_rejected(unknown_value: bool) -> None:
    config_data = load_config().model_dump(mode="json")
    config_data["system"]["parameter_catalog"]["definitions"].append(
        {
            "id": "frequencies",
            "value_type": {
                "shape": "table",
                "primary_key": ["frequency"],
                "columns": [
                    {
                        "id": "frequency",
                        "value_type": {
                            "type": "quantity",
                            "dimension": "frequency",
                        },
                    }
                ],
            },
        }
    )
    config_data["parameter_snapshot"]["values"].append(
        {
            "id": "frequencies",
            "shape": "table",
            "rows": [
                {"frequency": {"value": 5.0, "unit": "GHz"}},
                {"frequency": {"value": 5000.0, "unit": "MHz"}},
            ],
        }
    )
    if unknown_value:
        config_data["system"]["parameter_catalog"]["definitions"][-1]["value_type"][
            "columns"
        ].append({"id": "amplitude", "value_type": {"type": "float"}})
    config = ConfigProfileSnapshot.model_validate(config_data)

    problems = validate_config_profile(config)

    assert bool(problems)
    assert problems[0].code == "invalid_parameter_value"
