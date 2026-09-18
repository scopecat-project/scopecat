"""Refresh at request creation while retaining every existing request's source."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from scopecat.authoring.experiments import Experiment, ExperimentRequest
from scopecat.records.author_revision import AuthorRevisionRef


@dataclass
class LiveExperiment[**P, ResultT]:
    """A typed callable selecting saved author code for each new request.

    The wrapper never changes existing requests, previews or running jobs. Source
    admission errors propagate instead of silently falling back to old code.
    """

    source_revision: Callable[[], AuthorRevisionRef]
    refresh: Callable[[], Experiment[P, ResultT]]
    _selected: Experiment[P, ResultT] | None = field(default=None, init=False)

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> ExperimentRequest[ResultT]:
        revision = self.source_revision()
        if self._selected is None or self._selected.code_revision != revision:
            selected = self.refresh()
            self._selected = selected
        return self._selected(*args, **kwargs)
