"""Validate and normalize configuration for compiler consumption."""

from __future__ import annotations

from scopecat.compiler.environment import ConfigEnvironment
from scopecat.config.parameter_resolution import resolve_config_parameters
from scopecat.config.profile_validation import validate_config_profile
from scopecat.kernel.errors import CheckFailed
from scopecat.records.config import ConfigProfileSnapshot


def build_config_environment(
    config: ConfigProfileSnapshot,
    *,
    allow_missing: bool = False,
) -> ConfigEnvironment:
    resolved = resolve_config_parameters(config, allow_missing=allow_missing)
    problems = (
        *validate_config_profile(config, include_parameter_values=False),
        *resolved.problems,
    )
    if problems:
        raise CheckFailed(problems)
    return ConfigEnvironment(
        config=config,
        parameters=resolved.data,
    )
