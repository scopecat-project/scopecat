"""One explicit default author workspace per IPython kernel."""

from __future__ import annotations

import importlib
import os
import sys
from collections.abc import Callable
from dataclasses import replace
from html import escape
from pathlib import Path
from threading import get_ident
from types import ModuleType
from typing import Protocol, cast, override

import scopecat.authoring.experiments as declarations
from scopecat.application.author_project import AuthorProject
from scopecat.application_locator import read_author_location, read_data_identity
from scopecat.author_workspaces import (
    author_workspace_id,
    local_author_workspaces,
)
from scopecat.authoring.experiments import Experiment
from scopecat.daemon.endpoint import (
    read_daemon_endpoint_record,
    resolve_daemon_endpoint,
)
from scopecat.project import Project, SourceExecutionContext, open_project
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.runtime_binding import APPLICATION_INSTANCE_NAME, load_runtime_binding


class ShellEvents(Protocol):
    def register(self, event: str, function: Callable[..., None]) -> None: ...
    def unregister(self, event: str, function: Callable[..., None]) -> None: ...


class NotebookShell(Protocol):
    user_ns: dict[str, object]
    events: ShellEvents


def _shell() -> NotebookShell:
    try:
        ipython = importlib.import_module("IPython")
    except ModuleNotFoundError as error:
        raise RuntimeError(
            "sc.notebook() requires an IPython/Jupyter kernel."
        ) from error
    get_shell = cast("Callable[[], NotebookShell | None]", ipython.get_ipython)
    shell = get_shell()
    if shell is None:
        raise RuntimeError(
            "Use sc.notebook() inside IPython; scripts use project.authoring()."
        )
    return shell


_workspace: NotebookSession | None = None


def _module_owned(module: ModuleType, root: Path, cache: Path) -> bool:
    spec = module.__spec__
    if spec is None:
        return False
    locations = spec.submodule_search_locations or (
        [spec.origin] if spec.origin else []
    )
    return any(
        Path(location).is_relative_to(root) or Path(location).is_relative_to(cache)
        for location in locations
    )


def notebook(
    start: str | Path = ".",
    *,
    live: bool = True,
    daemon: str | None = None,
    application: str | Path | None = None,
) -> NotebookSession:
    """Open/reuse the kernel's default workspace without starting an experiment.

    Close the existing session before selecting a different project. A shared
    kernel shares this workspace; it is not scoped to a Notebook document.
    """
    return NotebookSession.open(
        start, live=live, daemon=daemon, application=application
    )


class NotebookSession(AuthorProject):
    """Author session with default request selection and cell-boundary imports."""

    @classmethod
    def open(
        cls,
        start: str | Path,
        *,
        live: bool,
        daemon: str | None,
        application: str | Path | None = None,
    ) -> NotebookSession:
        global _workspace
        shell = _shell()
        project = open_project(start)
        target = application or os.environ.get("SCOPECAT_APPLICATION_ROOT")
        if target is None:
            location = read_author_location(project.root)
            target = location.application if location else None
        binding = load_runtime_binding(project.root, target=target)
        environment = project.root / ".venv"
        registered = any(
            item.root == project.root
            for item in local_author_workspaces(project.root, target=target)
        )
        if registered:
            service_root = Path(target).resolve() if target else project.root
            if (service_root / APPLICATION_INSTANCE_NAME).is_file():
                record = read_daemon_endpoint_record(project.root, target=target)
                if record is not None and record.python is not None:
                    # Resolve directory aliases, not the executable symlink:
                    # independent venvs may share the same underlying binary.
                    application_python = (
                        record.python.parent.resolve() / record.python.name
                    )
                    kernel_python = Path(sys.executable)
                    kernel_python = kernel_python.parent.resolve() / kernel_python.name
                    if kernel_python == application_python:
                        raise RuntimeError(
                            "Select Kernel: choose an independent author environment, "
                            "not the application's Python"
                        )
        # Registered workspaces may retain .venv while selecting another kernel.
        # _default_request_revision below still validates imports against the
        # source captured by the selected execution environment; registration
        # does not make an incompatible client eligible to import that source.
        if (
            not registered
            and environment.is_dir()
            and Path(sys.prefix).resolve() != environment.resolve()
        ):
            raise RuntimeError(
                f"Select Kernel: choose the project's environment {environment}"
            )
        if (
            _workspace is not None
            and not _workspace.is_closed
            and (
                _workspace.project_root != project.root
                or _workspace._shell is not shell
            )
        ):
            raise ValueError(
                "Close the active Notebook workspace before switching projects."
            )
        identity = author_workspace_id(project.root, target=target)
        project = replace(
            project, execution=SourceExecutionContext(binding, identity, None)
        )
        endpoint = resolve_daemon_endpoint(project.root, explicit=daemon, target=target)
        if _workspace is not None and not _workspace.is_closed:
            if (
                _workspace.base_url != endpoint.rstrip("/")
                or _workspace.workspace_id != identity
                or _workspace._binding != binding
            ):
                raise ValueError(
                    "Close the active Notebook workspace before changing its service."
                )
            _workspace.live_enabled = live
            _workspace._default_request_revision()
            return _workspace
        session = cls(project, endpoint, shell, live=live)
        try:
            session.pin_session(
                project_id=read_data_identity(binding.data_root),
                source_root=project.root,
            )
            session._default_request_revision()
            shell.events.register("pre_run_cell", session._before_cell)
        except BaseException:
            session.close()
            raise
        _workspace = session
        declarations.request_selector = session
        session._registered = True
        return session

    def __init__(
        self, project: Project, endpoint: str, shell: NotebookShell, *, live: bool
    ) -> None:
        super().__init__(
            endpoint,
            receipts=project.runtime_binding.data_root / "author-jobs",
            project_root=project.root,
            source_project=project,
            workspace_id=project.execution.workspace_id if project.execution else None,
        )
        self._binding = project.runtime_binding
        self.live_enabled = live
        self._shell = shell
        self._thread = get_ident()
        self._revision: AuthorRevisionRef | None = None
        self._refresh_error: str | None = None
        self._selecting = False
        self._registered = False

    def _ensure_current(self) -> None:
        from scopecat.application.author_imports import (
            notebook_imports_selected,
            refresh_revision_imports,
        )

        root, cache = self._require_local_authoring()
        revision = (
            self._live_revision()
            if self.live_enabled or self._revision is None
            else self._revision
        )
        if revision == self._revision and notebook_imports_selected(root, revision):
            self._refresh_error = None
            return
        # Rebind top-level module aliases, never mutate retained module objects.
        aliases = {
            key: value
            for key, value in self._shell.user_ns.copy().items()
            if isinstance(value, ModuleType) and _module_owned(value, root, cache)
        }
        if revision != self._revision:
            state = self.refresh()
            self._revision = state.active
        else:
            catalog = self.catalog(code_revision=revision)
            refresh_revision_imports(
                self.author_revision(revision),
                project_root=root,
                cache=cache,
                fingerprints={entry.id: entry.version for entry in catalog.entries},
            )
        self._refresh_error = None
        for key, old in aliases.items():
            current = sys.modules.get(old.__name__)
            if current is not None and current is not old:
                self._shell.user_ns[key] = current

    def _before_cell(self, info: object) -> None:
        _ = info
        self._selecting = True
        try:
            self._ensure_current()
        except Exception as error:
            # IPython event exceptions do not abort the cell. Keep repair/history
            # cells usable; retry at request creation to prevent stale runs.
            self._refresh_error = str(error)
            print(
                "Scopecat refresh failed; new experiment requests are blocked "
                f"until fixed.\n{error}"
            )
        finally:
            self._selecting = False

    @override
    def _default_request_revision(self) -> AuthorRevisionRef:
        self._selecting = True
        try:
            self._ensure_current()
            assert self._revision is not None
            return self._revision
        except Exception as error:
            self._refresh_error = str(error)
            raise
        finally:
            self._selecting = False

    def select[**P, T](self, experiment: Experiment[P, T]) -> Experiment[P, T]:
        if get_ident() != self._thread or self._selecting:
            return experiment
        try:
            source = experiment.source
        except TypeError:
            # Interactive declarations have no managed file revision.
            return experiment
        self._selecting = True
        try:
            self._ensure_current()
            from scopecat.application.author_imports import select_notebook_experiment

            assert self._revision is not None
            root, cache = self._require_local_authoring()
            return select_notebook_experiment(
                experiment, source, self._revision, project_root=root, cache=cache
            )
        except Exception as error:
            self._refresh_error = str(error)
            raise
        finally:
            self._selecting = False

    @override
    def close(self) -> None:
        global _workspace
        if self._registered:
            self._shell.events.unregister("pre_run_cell", self._before_cell)
            self._registered = False
        if declarations.request_selector is self:
            declarations.request_selector = None
        if _workspace is self:
            _workspace = None
            from scopecat.application.author_imports import release_notebook_imports

            assert self.project_root is not None
            release_notebook_imports(self.project_root)
        super().close()

    @override
    def __repr__(self) -> str:
        mode = "live" if self.live_enabled else "fixed"
        revision = self._revision.content_hash[:12] if self._revision else "unselected"
        status = "closed" if self.is_closed else self._refresh_error or "ready"
        science = self.selection.science
        subject = science.subject
        subject_label = (
            f"target {subject.ref.target_id} r{subject.ref.revision}"
            if subject.kind == "registered_target"
            else subject.sample_id
            if subject.kind == "sample"
            else "unselected"
        )
        configuration = science.configuration
        configuration_label = (
            configuration.ref.revision_id
            if configuration.kind == "parameters"
            else configuration.kind
        )
        if self.selection.parameter_branch is not None:
            configuration_label = (
                f"branch {self.selection.parameter_branch} ({configuration_label})"
            )
        batch_label = (
            science.batch.id if science.batch.kind == "declared" else "unspecified"
        )
        return (
            f"Scopecat Notebook ({mode}, {status})\nProject: {self.project_root}\n"
            f"Source: {revision}\n"
            f"Subject: {subject_label}\n"
            f"Batch: {batch_label}\n"
            f"Configuration: {configuration_label}\n"
            f"Collection: {self.selection.collection or 'store history'}\n"
            f"Operator: {self.selection.operator}\nHistory: session.history()"
        )

    def _repr_html_(self) -> str:
        return f"<pre>{escape(repr(self))}</pre>"
