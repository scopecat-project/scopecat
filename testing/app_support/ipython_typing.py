"""Typed view of IPython's execution boundary (upstream namespace is untyped)."""

from typing import Protocol, cast

from IPython.core.interactiveshell import ExecutionResult, InteractiveShell


class Shell(Protocol):
    user_ns: dict[str, object]

    def run_cell(self, raw_cell: str) -> ExecutionResult: ...


def execution_shell(shell: InteractiveShell) -> Shell:
    # IPython exposes a dynamically typed namespace and incomplete method signatures.
    return cast("Shell", shell)  # pyright: ignore[reportInvalidCast]
