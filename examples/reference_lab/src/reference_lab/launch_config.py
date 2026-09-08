"""One reviewed configuration binding for all maintained launch procedures."""

from scopecat.api.lab import LabClient
from scopecat.application.launch import LaunchRequest
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.run import ConfigRegistryRunConfigSource


def launch_config(
    lab: LabClient, request: LaunchRequest
) -> tuple[ConfigProfileSnapshot, ConfigRegistryRunConfigSource]:
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
