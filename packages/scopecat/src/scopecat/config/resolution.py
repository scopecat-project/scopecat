"""Configuration validation shared by bootstrap and registry imports."""

from __future__ import annotations

from scopecat.config.profile_validation import (
    validate_config_profile as validate_planning_config,
)
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.ids import artifact_slug
from scopecat.records.config import (
    ConfigProfileSnapshot,
    SystemSpec,
    config_content_hash,
)
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.setup import ExecutableSetupSnapshot


def compose_configuration(
    setup: ExecutableSetupSnapshot,
    *,
    id: str,
    system_id: str,
    catalog: ParameterCatalog,
    parameters: ParameterSnapshot,
) -> ConfigProfileSnapshot:
    """Resolve separate setup and parameter inputs into an execution snapshot.

    This pure operation validates the combination but saves or selects nothing.
    The combined snapshot is still the current compiler/registry carrier, not
    the ownership boundary for the supplied inputs.
    """
    return validate_config_profile(
        ConfigProfileSnapshot(
            id=id,
            system=SystemSpec.model_validate(
                {**setup.model_dump(), "id": system_id, "parameter_catalog": catalog}
            ),
            parameter_snapshot=parameters,
        )
    )


def config_revision_entry_id(config: ConfigProfileSnapshot) -> str:
    """Return a deterministic registry id for one immutable config revision."""

    digest = config_content_hash(config).removeprefix("sha256:")[:12]
    return f"{artifact_slug(config.id, fallback='config')}-{digest}"


def validate_config_profile(
    config: ConfigProfileSnapshot,
) -> ConfigProfileSnapshot:
    """Validate infrastructure and present parameter values; unknowns are permitted."""

    problems = validate_planning_config(config)
    if problems:
        raise CheckFailed(problems)
    return config


__all__ = [
    "compose_configuration",
    "config_revision_entry_id",
    "validate_config_profile",
]
