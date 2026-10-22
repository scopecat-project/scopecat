"""Provenance of a currently executing durable effect, not an admission grant."""

from contextvars import ContextVar

# Transport validates this operation against the live durable step. It cannot
# authorize a new procedure, answer, retry, or unrelated execution stage.
procedure_effect: ContextVar[str | None] = ContextVar("procedure_effect", default=None)
PROCEDURE_EFFECT_HEADER = "X-Scopecat-Procedure-Effect"

retained_effect: ContextVar[str | None] = ContextVar("retained_effect", default=None)
RETAINED_EFFECT_HEADER = "X-Scopecat-Retained-Effect"
