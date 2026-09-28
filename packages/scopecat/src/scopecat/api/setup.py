"""Explicit notebook operations for the daemon's executable setup authority."""

from dataclasses import dataclass

from scopecat.daemon.client import DaemonClient
from scopecat.daemon.wire import (
    ConfigurationTemplateImportCommand,
    ConfigurationTemplateImportResult,
    ConfigurationTemplateView,
    SetupImportCommand,
    SetupSaveCommand,
)
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.setup import (
    ExecutableSetupSnapshot,
    SetupDefinition,
    SetupDefinitionRevision,
    SetupRevision,
)


@dataclass(frozen=True, slots=True)
class LabSetupOperations:
    client: DaemonClient
    operator: str

    def templates(self) -> tuple[ConfigurationTemplateView, ...]:
        """Discover fixed recipes offered by this service's installed adapter."""
        return self.client.configuration_templates().items

    def import_template(
        self, template: ConfigurationTemplateView, *, name: str, note: str = ""
    ) -> ConfigurationTemplateImportResult:
        """Save reviewed setup and complete parameters, without activating either.

        Use a stable name to retry. Select the returned setup explicitly and use
        parameters with session.use(parameters=result.parameters, setup=result.setup),
        or use result.selection to retain the exact imported setup as well.
        """
        return self.client.import_configuration_template(
            ConfigurationTemplateImportCommand(
                template_id=template.id,
                content_hash=template.content_hash,
                revision_id=name,
                actor=self.operator,
                note=note,
            )
        )

    def get(self, name: str) -> SetupRevision:
        """Resolve a named definition against the current registered devices."""
        return self.client.resolve_setup(name)

    def revision(self, revision_id: str) -> SetupRevision:
        """Read an exact retained resolution without following device heads."""
        return self.client.setup_revision(revision_id)

    def definition(self, name: str) -> SetupDefinitionRevision:
        return self.client.setup_definition(name)

    def list(self) -> tuple[SetupDefinitionRevision, ...]:
        return self.client.setup_definitions().items

    def save(
        self,
        setup: SetupDefinition,
        *,
        name: str,
        note: str = "",
    ) -> SetupRevision:
        """Save a named immutable revision; saving does not select it."""
        return self.client.save_setup(
            SetupSaveCommand(
                revision_id=name, setup=setup, actor=self.operator, note=note
            )
        )

    def import_recipe(
        self,
        setup: ExecutableSetupSnapshot | ConfigProfileSnapshot,
        *,
        name: str,
        note: str = "",
    ) -> SetupRevision:
        """Register a recipe's devices and save references; never update connections."""
        snapshot = (
            ExecutableSetupSnapshot.from_config(setup)
            if isinstance(setup, ConfigProfileSnapshot)
            else setup
        )
        return self.client.import_setup(
            SetupImportCommand(
                revision_id=name, setup=snapshot, actor=self.operator, note=note
            )
        )


__all__ = ["LabSetupOperations"]
