"""Independent parameter revisions and explicit execution-input preparation."""

from dataclasses import dataclass

from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import ConfigEntryView, ParameterResolution
from scopecat.daemon.wire import (
    ParameterBindCommand,
    ParameterResolveCommand,
    ParameterSaveCommand,
)
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.parameter_revision import ParameterRevision, ParameterRevisionRef
from scopecat.records.setup import SetupRevision, SetupRevisionRef


@dataclass(frozen=True, slots=True)
class LabParameterOperations:
    client: DaemonClient
    operator: str

    def save(
        self,
        *,
        name: str,
        catalog: ParameterCatalog,
        parameters: ParameterSnapshot,
        note: str = "",
    ) -> ParameterRevision:
        """Save declarations and values; no setup, sample or default is required.

        Use a new name for changed content. Repeating identical input under the
        same name returns the original revision. Saving asserts no calibration
        validity and changes no measurement context.
        """
        return self.client.save_parameters(
            ParameterSaveCommand(
                revision_id=name,
                catalog=catalog,
                parameters=parameters,
                actor=self.operator,
                note=note,
            )
        )

    def get(self, name: str) -> ParameterRevision:
        return self.client.parameter_revision(name)

    def list(self) -> tuple[ParameterRevision, ...]:
        return self.client.parameter_revisions().items

    def bind(
        self,
        revision: ParameterRevision | ParameterRevisionRef,
        *,
        setup: SetupRevision | SetupRevisionRef,
        name: str,
        system_id: str,
        note: str = "",
    ) -> ConfigEntryView:
        """Save an exact execution combination without activating either owner.

        Select the result through the existing saved-configuration measurement
        context. This validates structural compatibility, not calibration reuse.
        Execution still requires a compatible independently selected setup.
        """
        return self.client.bind_parameters(
            ParameterBindCommand(
                parameters=revision.ref
                if isinstance(revision, ParameterRevision)
                else revision,
                setup=setup.ref if isinstance(setup, SetupRevision) else setup,
                entry_id=name,
                system_id=system_id,
                actor=self.operator,
                note=note,
            )
        )

    def resolve(
        self,
        revision: ParameterRevision | ParameterRevisionRef,
        *,
        setup: SetupRevision | SetupRevisionRef | None = None,
    ) -> ParameterResolution:
        """Resolve exact run inputs without saving a combined registry entry.

        If setup is omitted, capture the current setup once. The result retains
        exact references and can be passed as `config` to the low-level runner.
        """
        selected = setup or self.client.active_setup().revision
        return self.client.resolve_parameters(
            ParameterResolveCommand(
                parameters=revision.ref
                if isinstance(revision, ParameterRevision)
                else revision,
                setup=selected.ref if isinstance(selected, SetupRevision) else selected,
            )
        )
