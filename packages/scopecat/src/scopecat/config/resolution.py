"""Configuration validation shared by bootstrap and registry imports."""

from __future__ import annotations

from scopecat.config.profile_validation import (
    validate_config_profile as validate_planning_config,
)
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.ids import artifact_slug
from scopecat.records.config import ConfigProfileSnapshot, config_content_hash


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


__all__ = ["config_revision_entry_id", "validate_config_profile"]
