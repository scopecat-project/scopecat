"""Validate complete configuration profiles."""

from __future__ import annotations

from scopecat.config.parameter_resolution import resolve_config_parameters
from scopecat.kernel.problems import (
    Problem,
    ProblemPhase,
    model_location,
    problem,
)
from scopecat.records.config import ConfigProfileSnapshot


def _problem(
    code: str,
    message: str,
    path: tuple[str | int, ...],
) -> Problem:
    return problem(
        f"configuration.{code}",
        message,
        phase=ProblemPhase.CONFIGURATION,
        location=model_location("config_profile", *path),
    )


def _routing_route_problems(
    config: ConfigProfileSnapshot,
) -> tuple[Problem, ...]:
    problems: list[Problem] = []
    path = ("system", "routing", "routes")
    instrument_ids = {
        instrument.id for instrument in config.instrument_registry.instruments
    }
    entity_ids = {entity.id for entity in config.topology.entities}

    for route in config.routing.routes:
        if route.instrument_id not in instrument_ids:
            problems.append(
                _problem(
                    "unknown_resource_route_instrument",
                    "resource route references unknown instrument "
                    f"{route.instrument_id}",
                    path,
                )
            )
        for entity_id in route.entity_ids:
            if entity_id not in entity_ids:
                problems.append(
                    _problem(
                        "unknown_resource_route_entity",
                        f"resource route references unknown entity {entity_id}",
                        path,
                    )
                )
        for endpoint in route.endpoints:
            if endpoint.entity_id is not None and endpoint.entity_id not in entity_ids:
                problems.append(
                    _problem(
                        "unknown_resource_route_entity",
                        "resource route references unknown entity "
                        f"{endpoint.entity_id}",
                        path,
                    )
                )
    return tuple(problems)


def validate_config_profile(
    config: ConfigProfileSnapshot,
    *,
    include_parameter_values: bool = True,
) -> tuple[Problem, ...]:
    problems: list[Problem] = []

    entity_ids = {entity.id for entity in config.topology.entities}
    if (
        config.primary_entity_id
        and entity_ids
        and config.primary_entity_id not in entity_ids
    ):
        problems.append(
            _problem(
                "unknown_primary_entity",
                "primary_entity_id references an unknown entity "
                f"{config.primary_entity_id}",
                ("system", "primary_entity_id"),
            )
        )

    problems.extend(_routing_route_problems(config))

    if include_parameter_values:
        problems.extend(resolve_config_parameters(config, allow_missing=True).problems)

    return tuple(problems)
