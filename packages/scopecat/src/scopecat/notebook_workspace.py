"""One explicit default author workspace per IPython kernel."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from html import escape
from pathlib import Path
from threading import get_ident
from types import ModuleType
from typing import Protocol, cast, override

import scopecat.authoring.experiments as declarations
from scopecat.application.author_project import AuthorProject
from scopecat.authoring.experiments import Experiment
from scopecat.daemon.endpoint import resolve_daemon_endpoint
from scopecat.project import Project, open_project
from scopecat.records.author_revision import AuthorRevisionRef


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
    start: str | Path = ".", *, live: bool = True, daemon: str | None = None
) -> NotebookSession:
    """Open/reuse the kernel's default workspace without starting an experiment.

    Close the existing session before selecting a different project. A shared
    kernel shares this workspace; it is not scoped to a Notebook document.
    """
    return NotebookSession.open(start, live=live, daemon=daemon)


class NotebookSession(AuthorProject):
    """Author session with default request selection and cell-boundary imports."""

    @classmethod
    def open(
        cls, start: str | Path, *, live: bool, daemon: str | None
    ) -> NotebookSession:
        global _workspace
        shell = _shell()
        project = open_project(start)
        environment = project.root / ".venv"
        if environment.is_dir() and Path(sys.prefix).resolve() != environment.resolve():
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
        endpoint = resolve_daemon_endpoint(project.root, explicit=daemon)
        if _workspace is not None and not _workspace.is_closed:
            if _workspace.base_url != endpoint.rstrip("/"):
                raise ValueError(
                    "Close the active Notebook workspace before changing its service."
                )
            _workspace.live_enabled = live
            _workspace._default_request_revision()
            return _workspace
        session = cls(project, endpoint, shell, live=live)
        try:
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
        )
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
        working_point = self.selection.working_point
        working_point_label = working_point.entry_id if working_point else "lab default"
        return (
            f"Scopecat Notebook ({mode}, {status})\nProject: {self.project_root}\n"
            f"Source: {revision}\n"
            f"Sample: {self.selection.sample or 'unselected'}\n"
            f"Working point: {working_point_label}\n"
            f"Collection: {self.selection.collection or 'store history'}\n"
            f"Operator: {self.selection.operator}\nHistory: session.history()"
        )

    def _repr_html_(self) -> str:
        return f"<pre>{escape(repr(self))}</pre>"
