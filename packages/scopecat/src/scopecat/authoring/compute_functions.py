"""Callable computations with an explicit native execution entry point."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol, cast

from scopecat.authoring._module_results import DataRef
from scopecat.program.products import ProductNativeValue, ProductRef
from scopecat.program.values import ComputeInput


class _ComputeRecorder(Protocol):
    def compute[T: ProductNativeValue](
        self,
        *,
        fn: Callable[..., T],
        inputs: Mapping[str, ComputeInput | ProductRef],
    ) -> DataRef[T]: ...


_current_context: ContextVar[_ComputeRecorder | None] = ContextVar(
    "scopecat_compute_context", default=None
)


@contextmanager
def compute_context_internal(
    context: _ComputeRecorder,
) -> Generator[None]:
    token = _current_context.set(context)
    try:
        yield
    finally:
        _current_context.reset(token)


@dataclass(frozen=True, slots=True)
class DeferredCompute[**P, T: ProductNativeValue]:
    """A graph-building callable; ``eager`` retains the native Python signature.

    Symbolic calls retain the result type. Argument names and required arguments
    are validated at graph construction, not statically transformed from ``P``.
    """

    eager: Callable[P, T]
    _signature: inspect.Signature

    def __call__(
        self, *args: ComputeInput | ProductRef, **kwargs: ComputeInput | ProductRef
    ) -> DataRef[T]:
        context = _current_context.get()
        if context is None:
            raise RuntimeError(
                "compute calls belong inside an experiment or module definition; "
                "use .eager(...) to execute with native values"
            )
        bound = self._signature.bind(*args, **kwargs)
        bound.apply_defaults()
        return context.compute(
            fn=self.eager,
            inputs=cast("Mapping[str, ComputeInput | ProductRef]", bound.arguments),
        )


def compute[**P, T: ProductNativeValue](
    fn: Callable[P, T],
) -> DeferredCompute[P, T]:
    """Defer calls in experiment definitions; execute native code via ``.eager``.

    Return annotations declare scalar or Annotated array/unit schemas. Positional
    and keyword-only arguments are supported; variadic and positional-only native
    signatures cannot be represented by the current named-input execution model.
    """
    signature = inspect.signature(fn)
    for parameter in signature.parameters.values():
        if parameter.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            raise TypeError(
                f"compute parameter {parameter.name!r} must be positional-or-keyword "
                "or keyword-only"
            )
    return DeferredCompute(fn, signature)
