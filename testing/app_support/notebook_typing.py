"""Narrow typed boundary for nbformat's incompletely annotated v4 API."""

from pathlib import Path
from typing import Protocol, cast

import nbformat
from nbformat import NotebookNode


class NotebookFactories(Protocol):
    def new_code_cell(self, source: str = "") -> NotebookNode: ...
    def new_markdown_cell(self, source: str = "") -> NotebookNode: ...
    def new_notebook(self, *, cells: list[NotebookNode]) -> NotebookNode: ...


class NotebookAPI(Protocol):
    # Use positional paths/documents: upstream names these parameters fp and nb.
    def read(self, path: Path, /, *, as_version: int) -> NotebookNode: ...
    def write(self, document: NotebookNode, path: Path, /) -> None: ...


# nbformat accepts these documented calls, but its kwargs/IO signatures are untyped.
_api = cast("NotebookAPI", nbformat)  # pyright: ignore[reportInvalidCast]
read = _api.read
write = _api.write
v4 = cast("NotebookFactories", nbformat.v4)  # pyright: ignore[reportInvalidCast]


def cells(document: NotebookNode) -> list[NotebookNode]:
    """Validate the document cell list, preserving its identity for edits."""
    value = cast("object", document["cells"])
    if not isinstance(value, list):
        raise TypeError("Expected a notebook document containing cells")
    items = cast("list[object]", value)
    if not all(isinstance(cell, NotebookNode) for cell in items):
        raise TypeError("Expected a notebook document containing NotebookNode cells")
    return cast("list[NotebookNode]", value)
