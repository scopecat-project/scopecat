"""Callable Python UX for immutable experiment invocations."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Generic, ParamSpec, TypeVar, cast

from scopecat.program.controls import ControlSet
from scopecat.program.definitions import ExperimentInvocation

type ExperimentBuilder[ResultT] = Callable[
    [Mapping[str, object]], ExperimentInvocation[ResultT]
]

_P = ParamSpec("_P")
_ExperimentResultT_co = TypeVar(
    "_ExperimentResultT_co",
    covariant=True,
    default=object,
)


@dataclass(frozen=True, slots=True)
class ExperimentInput:
    """One resolved function input, available without constructing a program."""

    name: str
    annotation: object
    default: object
    runtime: bool

    @property
    def required(self) -> bool:
        return self.default is inspect.Parameter.empty


@dataclass(frozen=True, slots=True, repr=False)
class Experiment(Generic[_P, _ExperimentResultT_co]):
    """One experiment authoring function with structural and runtime inputs."""

    _callable: Callable[_P, _ExperimentResultT_co] = field(
        repr=False,
        compare=False,
    )
    _signature: inspect.Signature = field(repr=False, compare=False)
    _builder: ExperimentBuilder[_ExperimentResultT_co] = field(
        repr=False,
        compare=False,
    )
    id: str
    kind: str
    metadata: Mapping[str, object] = field(repr=False)
    inputs: tuple[ExperimentInput, ...]
    controls: ControlSet

    @property
    def __wrapped__(self) -> Callable[_P, _ExperimentResultT_co]:
        return self._callable

    @property
    def __name__(self) -> str:
        return self._callable.__name__

    @property
    def __signature__(self) -> inspect.Signature:
        return self._signature

    def __call__(
        self,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> ExperimentInvocation[_ExperimentResultT_co]:
        """Build one immutable invocation from structural and runtime arguments."""

        bound = self._signature.bind(*args, **kwargs)
        return self._builder(cast("Mapping[str, object]", bound.arguments))

    def bind(self, **inputs: object) -> ExperimentInvocation[_ExperimentResultT_co]:
        """Build with complete structural args and partial runtime inputs."""

        bound = self._signature.bind_partial(**inputs)
        return self._builder(cast("Mapping[str, object]", bound.arguments))


__all__ = [
    "Experiment",
    "ExperimentInvocation",
]
