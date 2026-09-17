"""Typed boundary for nbformat's dynamically typed notebook constructors."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from nbformat import NotebookNode


class NotebookFactory(Protocol):
    def new_code_cell(self, source: str) -> NotebookNode: ...
    def new_notebook(self, *, cells: list[NotebookNode]) -> NotebookNode: ...


class NotebookIO(Protocol):
    v4: NotebookFactory

    def read(self, path: Path, *, as_version: int) -> NotebookNode: ...
    def write(self, notebook: NotebookNode, path: Path) -> None: ...


def notebook_io() -> NotebookIO:
    return cast("NotebookIO", cast("object", import_module("nbformat")))
