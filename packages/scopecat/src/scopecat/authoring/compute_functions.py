"""Callable computations with an explicit native execution entry point."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol, cast, overload

from scopecat.authoring._module_results import DataRef
from scopecat.program.products import ProductNativeValue, ProductRef, ProductRefs
from scopecat.program.value_refs import ValueRef
from scopecat.program.value_types import Array, Scalar
from scopecat.program.values import ComputeInput

type ComputeOutput = Scalar | Array | Callable[..., Scalar | Array]


class _ComputeRecorder(Protocol):
    def compute(
        self,
        *,
        fn: Callable[..., ProductNativeValue],
        inputs: Mapping[str, ComputeInput | ProductRef],
        output_type: Scalar | Array | None = None,
    ) -> ValueRef | ProductRef | ProductRefs: ...


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
    _output_type: ComputeOutput | None = None

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
        output_type = self._output_type
        if callable(output_type):
            schema_arguments = {
                name: bound.arguments[name]
                for name in inspect.signature(output_type).parameters
            }
            output_type = output_type(**schema_arguments)
        return cast(
            "DataRef[T]",
            context.compute(
                fn=self.eager,
                output_type=output_type,
                inputs=cast("Mapping[str, ComputeInput | ProductRef]", bound.arguments),
            ),
        )


@overload
def compute[**P, T: ProductNativeValue](
    fn: Callable[P, T],
    *,
    output_type: ComputeOutput | None = None,
) -> DeferredCompute[P, T]: ...


@overload
def compute[**P, T: ProductNativeValue](
    *,
    output_type: ComputeOutput,
) -> Callable[[Callable[P, T]], DeferredCompute[P, T]]: ...


def compute[**P, T: ProductNativeValue](
    fn: Callable[P, T] | None = None,
    *,
    output_type: ComputeOutput | None = None,
) -> DeferredCompute[P, T] | Callable[[Callable[P, T]], DeferredCompute[P, T]]:
    """Defer calls in definitions; use ``.eager`` for native execution.

    Return annotations normally declare the output schema. ``output_type`` may
    override it with a schema or a factory whose named arguments select structural
    inputs from the native function (for example, an array's shot count).
    """
    if fn is None:

        def decorate(native: Callable[P, T]) -> DeferredCompute[P, T]:
            return compute(native, output_type=output_type)

        return decorate
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
    if callable(output_type):
        for name, parameter in inspect.signature(output_type).parameters.items():
            if name not in signature.parameters or parameter.kind not in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                raise TypeError(
                    f"output schema argument {name!r} must name a compute input"
                )
    return DeferredCompute(fn, signature, output_type)
