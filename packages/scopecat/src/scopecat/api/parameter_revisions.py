"""Independent parameter revisions and explicit execution-input preparation."""

from dataclasses import dataclass, field
from uuid import uuid4

from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import ConfigEntryView, ParameterResolution
from scopecat.daemon.wire import (
    ParameterBindCommand,
    ParameterBranchCommitCommand,
    ParameterResolveCommand,
    ParameterSaveCommand,
)
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.parameter_revision import ParameterRevision, ParameterRevisionRef
from scopecat.records.setup import SetupRevision, SetupRevisionRef


@dataclass(frozen=True, slots=True)
class LabParameterOperations:
    client: DaemonClient
    operator: str

    def create_branch(
        self,
        name: str,
        *,
        revision: ParameterRevision | ParameterRevisionRef,
        note: str = "",
    ) -> ParameterBranch:
        """Create a named history from existing values without selecting defaults."""
        return self.client.commit_parameter_branch(
            ParameterBranchCommitCommand(
                name=name,
                expected_generation=0,
                source=revision.ref
                if isinstance(revision, ParameterRevision)
                else revision,
                actor=self.operator,
                note=note,
            )
        )

    def checkout(self, name: str) -> ParameterBranchWorkspace:
        """Capture an editing base; concurrent saves will not be overwritten."""
        return ParameterBranchWorkspace(self, self.client.get_parameter_branch(name))

    def history(self, name: str) -> tuple[ParameterBranch, ...]:
        return self.client.parameter_branch_history(name).items

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


@dataclass(slots=True)
class ParameterBranchWorkspace:
    """A local editing base for one named branch."""

    operations: LabParameterOperations
    head: ParameterBranch
    _pending: ParameterBranchCommitCommand | None = field(
        default=None, init=False, repr=False
    )

    @property
    def revision(self) -> ParameterRevision:
        return self.operations.get(self.head.revision.revision_id)

    def save(
        self,
        *,
        catalog: ParameterCatalog,
        parameters: ParameterSnapshot,
        note: str = "",
    ) -> ParameterBranch:
        """Save a revision and advance this branch atomically, or report a conflict."""
        source = ParameterSaveCommand(
            revision_id=f"parameters-{uuid4().hex}",
            catalog=catalog,
            parameters=parameters,
            actor=self.operations.operator,
            note=note,
        )
        pending = self._pending
        if (
            pending is None
            or not isinstance(pending.source, ParameterSaveCommand)
            or pending.source.model_dump(exclude={"revision_id"})
            != source.model_dump(exclude={"revision_id"})
        ):
            pending = ParameterBranchCommitCommand(
                name=self.head.name,
                expected_generation=self.head.generation,
                source=source,
                actor=self.operations.operator,
                note=note,
            )
        self._pending = pending
        self.head = self.operations.client.commit_parameter_branch(pending)
        self._pending = None
        return self.head
