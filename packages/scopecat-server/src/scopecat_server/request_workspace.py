"""Request-local registered source identity; never a client-selected ownership scope."""

from contextvars import ContextVar

request_workspace: ContextVar[str | None] = ContextVar(
    "request_workspace", default=None
)
