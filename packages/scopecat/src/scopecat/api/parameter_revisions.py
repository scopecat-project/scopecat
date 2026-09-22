"""Independent parameter revisions and explicit execution-input preparation."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Self, override
from uuid import uuid4

from scopecat.api.parameters import ParameterEditor
from scopecat.config.candidate_merges import merge_parameter_branches
from scopecat.config.parameter_updates import materialize_context_updates
from scopecat.config.structure import ParameterContentPreview, preview_parameter_content
from scopecat.daemon.client import DaemonClient
from scopecat.daemon.views import ConfigEntryView, ParameterResolution
from scopecat.daemon.wire import (
    ParameterBindCommand,
    ParameterBranchCommitCommand,
    ParameterBranchPage,
    ParameterResolveCommand,
    ParameterSaveCommand,
)
from scopecat.records.parameter import ParameterCatalog, ParameterSnapshot
from scopecat.records.parameter_branch import ParameterBranch
from scopecat.records.parameter_content import ParameterContent
from scopecat.records.parameter_revision import ParameterRevision, ParameterRevisionRef
from scopecat.records.parameter_structure import ParameterStructureEdit
from scopecat.records.parameter_update import ParameterUpdate
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

    def workspace(self, name: str) -> BranchParameterEditor:
        """Open the table/scalar editor without requiring a setup or sample."""
        return BranchParameterEditor(self.checkout(name))

    def history(self, name: str) -> tuple[ParameterBranch, ...]:
        return self.client.parameter_branch_history(name).items

    def branches(
        self, *, limit: int = 100, after: str | None = None
    ) -> ParameterBranchPage:
        """Browse current heads by name; use next_cursor for subsequent pages."""
        return self.client.parameter_branches(limit=limit, after=after)

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
        overrides: tuple[ParameterUpdate, ...] = (),
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
                overrides=overrides,
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


class BranchParameterEditor(ParameterEditor):
    """Table edits and schema changes on a checked-out parameter branch."""

    def __init__(self, branch: ParameterBranchWorkspace) -> None:
        self._branch = branch
        self._head = branch.head
        self._revision = branch.operations.get(self._head.revision.revision_id)
        self._pending: ParameterBranchCommitCommand | None = None
        self._initialize_buffers()

    @property
    def branch(self) -> str:
        return self._head.name

    @property
    @override
    def _label(self) -> str:
        return f"Branch {self.branch}"

    @override
    def __repr__(self) -> str:
        return (
            f"ParameterWorkspace(branch={self.branch!r}, tables={len(self)}, "
            f"edits={len(self.diff())}, structure_edits={len(self._structure)})"
        )

    @property
    def version(self) -> ParameterRevision:
        return self._revision

    @property
    def _content(self) -> ParameterContent:
        return ParameterContent(
            parameter_catalog=self._revision.catalog,
            parameter_snapshot=self._revision.parameters,
        )

    @property
    @override
    def _saved_snapshot(self) -> ParameterSnapshot:
        return self._revision.parameters

    @property
    @override
    def _baseline(self) -> ParameterContent:
        preview = self.structure_diff()
        return preview.content if preview else self._content

    def structure_diff(self) -> ParameterContentPreview | None:
        return (
            preview_parameter_content(self._content, edits=tuple(self._structure))
            if self._structure
            else None
        )

    @override
    def _stage_structure(self, edits: Sequence[ParameterStructureEdit]) -> None:
        if self.diff():
            raise ValueError(
                "Save or discard value edits before changing the table structure"
            )
        preview = preview_parameter_content(
            self._content, edits=(*self._structure, *edits)
        )
        for item in edits:
            if item.parameter_id in self._data:
                self._data[item.parameter_id].tokens.clear()
        self._structure.extend(edits)
        self._load(preview.content.parameter_snapshot)

    @override
    def _copy_base(self) -> Self:
        return type(self)(ParameterBranchWorkspace(self._branch.operations, self._head))

    def save(self, name: str | None = None, *, note: str = "") -> ParameterRevision:
        """Save edits, or fork a named branch; unchanged ordinary saves are no-ops."""
        if name is None and not self.diff() and not self._structure:
            return self.version
        content = self._baseline
        parameters = materialize_context_updates(
            catalog=content.parameter_catalog,
            base=content.parameter_snapshot,
            updates=self._updates(),
        )
        source = ParameterSaveCommand(
            revision_id=f"parameters-{uuid4().hex}",
            catalog=content.parameter_catalog,
            parameters=parameters,
            actor=self._branch.operations.operator,
            note=note,
        )
        command = ParameterBranchCommitCommand(
            name=name if name is not None else self.branch,
            expected_generation=0 if name is not None else self._head.generation,
            source=source,
            actor=source.actor,
            note=note,
        )
        pending = self._pending
        if (
            pending is not None
            and pending.model_dump(exclude={"source"})
            == command.model_dump(exclude={"source"})
            and isinstance(pending.source, ParameterSaveCommand)
            and pending.source.model_dump(exclude={"revision_id"})
            == source.model_dump(exclude={"revision_id"})
        ):
            command = pending
        self._pending = command
        saved = self._branch.operations.client.commit_parameter_branch(command)
        revision = self._branch.operations.get(saved.revision.revision_id)
        self._head = saved
        self._branch.head = saved
        self._revision = revision
        self._pending = None
        self._structure.clear()
        self._load(revision.parameters)
        return revision

    def rebase(self) -> None:
        """Merge independent cells from the latest branch; retain drafts on conflict."""
        if self._structure:
            raise ValueError("Save or discard structure changes before rebasing")
        selected = self._branch.operations.checkout(self.branch)
        revision = selected.revision
        if revision.catalog != self._revision.catalog:
            raise ValueError(
                "parameter schema changed; reopen and review the new table structure"
            )
        local = materialize_context_updates(
            catalog=self._revision.catalog,
            base=self._revision.parameters,
            updates=self._updates(),
        )
        merged = merge_parameter_branches(
            base=self._content, local=local, current=revision.parameters
        )
        self._head = selected.head
        self._branch.head = selected.head
        self._revision = revision
        self._pending = None
        self._load(merged)

    @override
    def freeze(self) -> ParameterResolution:
        if self._structure:
            raise ValueError(
                "Save parameter structure changes before preparing an experiment"
            )
        return self._branch.operations.resolve(
            self._revision, overrides=self._updates()
        )

    def preview(self) -> ParameterResolution:
        return self.freeze()
