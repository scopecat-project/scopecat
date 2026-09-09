"""One context resolver and reviewed binding for maintained and authored launchers."""

from typing import TYPE_CHECKING

from scopecat.application.launch import LaunchConfigSource, LaunchRequest
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.run import ConfigRegistryRunConfigSource

if TYPE_CHECKING:
    from scopecat.api.lab import LabClient


def resolve_launch_config(
    lab: LabClient, request: LaunchRequest
) -> tuple[ConfigProfileSnapshot, LaunchConfigSource]:
    if request.context is not None:
        resolved = lab.config.resolve_context(
            request.context, overrides=request.overrides
        )
        source = resolved.config_source
        if request.sample is not None and request.sample != source.sample.sample_id:
            raise ValueError("launch sample does not match the selected context")
        if request.action == "submit" and request.config_source != source:
            raise ValueError(
                "context or lab configuration changed since preview; preview again"
            )
        return resolved.config, source
    if isinstance(request.config_source, ContextRunConfigSource):
        raise ValueError(
            "context source requires the matching explicit context selection"
        )
    if request.action == "preview":
        config, source = lab.config.resolve_with_source("active")
        assert isinstance(source, ConfigRegistryRunConfigSource)
        return config, source
    source = request.config_source
    assert source is not None and source.registry_generation is not None
    snapshot = lab.config.entry(source.entry_id)
    if (
        source.selector != "active"
        or source.config_ref != snapshot.entry.config_ref
        or source.content_hash != snapshot.entry.content_hash
    ):
        raise ValueError(
            "preview configuration reference does not match its immutable snapshot"
        )
    active = lab.config.active()
    if (
        active.activation.generation == source.registry_generation
        and active.entry.id != source.entry_id
    ):
        raise ValueError("preview configuration binding does not match its generation")
    return snapshot.config, source


def launch_config_generation(source: LaunchConfigSource) -> int:
    if isinstance(source, ContextRunConfigSource):
        return source.lab_generation
    assert source.registry_generation is not None
    return source.registry_generation
