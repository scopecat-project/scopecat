"""Notebook history presentation without changing durable run identifiers."""

from dataclasses import dataclass
from html import escape
from typing import override

from scopecat.daemon.views import RunSummaryPage


@dataclass(frozen=True, repr=False)
class RunHistory:
    """A captured page; numbers select runs in this project, not list positions."""

    page: RunSummaryPage
    collection: str | None = None

    @property
    def next_cursor(self) -> int | None:
        return self.page.next_cursor

    def _rows(self) -> list[tuple[str, str, str, str]]:
        return [
            (
                str(
                    item.address.number
                    if self.collection is not None and item.address is not None
                    else item.control.sequence
                ),
                item.snapshot.created_at.astimezone().isoformat(timespec="seconds"),
                item.control.admission.display_name or item.run_id,
                item.control.state,
            )
            for item in self.page.items
        ]

    @property
    def _hint(self) -> str:
        return (
            f"Select with session.run(number, collection={self.collection!r})."
            if self.collection is not None
            else "Select with session.run(number). Numbers belong to this project."
        )

    @override
    def __repr__(self) -> str:
        return "\n".join(
            ["Run # | Local time | Experiment | State"]
            + [" | ".join(row) for row in self._rows()]
            + [self._hint]
        )

    def _repr_html_(self) -> str:
        headings = "".join(
            f"<th>{name}</th>"
            for name in ("Run #", "Local time", "Experiment", "State")
        )
        rows = "".join(
            "<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in row) + "</tr>"
            for row in self._rows()
        )
        return (
            f"<table><thead><tr>{headings}</tr></thead><tbody>{rows}</tbody></table>"
            f"<p>{escape(self._hint)}</p>"
        )
