"""Read-only executable setup access shared by runtime and application services."""

from typing import Protocol

from scopecat.records.config import ConfigProfileSnapshot, SystemSpec
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.setup import ActiveSetupView, SetupRevision


class SetupReader(Protocol):
    """Resolve current authority or the immutable revision pinned by a session."""

    def current(self) -> ActiveSetupView: ...

    def get(self, revision_id: str) -> SetupRevision: ...


def setup_config(revision: SetupRevision) -> ConfigProfileSnapshot:
    """Adapt executable content to existing instrument-only config consumers."""
    setup = revision.setup
    return ConfigProfileSnapshot(
        id=revision.id,
        system=SystemSpec(
            id=revision.id,
            primary_entity_id=setup.primary_entity_id,
            topology=setup.topology,
            instrument_registry=setup.instrument_registry,
            routing=setup.routing,
            domain_target=setup.domain_target,
            scenario=setup.scenario,
            parameter_catalog=ParameterCatalog(id=revision.id),
        ),
        parameter_snapshot=ParameterSnapshot(id=revision.id),
    )
