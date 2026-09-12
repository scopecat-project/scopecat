"""Callable Python UX for immutable experiment invocations."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Generic, ParamSpec, SupportsFloat, TypeVar, cast

from scopecat.kernel.python_source import python_source_identity
from scopecat.kernel.quantity import Quantity
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
    _source: Mapping[str, str] | None = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Capture while this callable is defined: rereading an edited file later
        # cannot identify the code already loaded in a notebook.
        try:
            source = python_source_identity(self._callable, label=self.id)
        except TypeError:
            # Interactive declarations still support local program construction.
            # Managed requests require a file-backed declaration at their boundary.
            captured = None
        else:
            captured = MappingProxyType(dict(source))
        object.__setattr__(self, "_source", captured)

    @property
    def source(self) -> Mapping[str, str]:
        """Lexical source captured at declaration time, not at preparation time."""
        if self._source is None:
            raise TypeError(f"{self.id} source must be available to fingerprint")
        return self._source

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

    def request(
        self,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> ExperimentRequest[_ExperimentResultT_co]:
        """Describe editable managed inputs without executing the experiment body.

        Creation retains the function's argument types. Dictionary edits are
        validated against the selected declaration when the request is prepared.
        """
        bound = self._signature.bind(*args, **kwargs)
        bound.apply_defaults()
        values = dict(bound.arguments)
        for control in self.controls.fields:
            if control.ownership == "editable":
                values.setdefault(control.id, control.default)
        return ExperimentRequest(self, deepcopy(values))

    def bind(self, **inputs: object) -> ExperimentInvocation[_ExperimentResultT_co]:
        """Build with complete structural args and partial runtime inputs."""

        bound = self._signature.bind_partial(**inputs)
        return self._builder(cast("Mapping[str, object]", bound.arguments))


@dataclass(frozen=True, slots=True, init=False)
class Scan:
    """An explicit numeric scan; arrays passed as values never imply a scan."""

    values: tuple[float | Quantity, ...]

    def __init__(self, values: Iterable[SupportsFloat | Quantity]) -> None:
        captured: list[float | Quantity] = []
        for value in values:
            if isinstance(value, bool):
                raise TypeError("scan values require numbers, not booleans")
            captured.append(value if isinstance(value, Quantity) else float(value))
        object.__setattr__(self, "values", tuple(captured))


@dataclass(frozen=True, slots=True)
class ExperimentRequest(Generic[_ExperimentResultT_co]):
    """Editable values tied to a declaration; contains no cached program or output.

    Mutate ``values`` or make an isolated ``copy()`` before exploring alternatives.
    ``author.prepare(request)`` captures the values and rebuilds on the selected
    managed source revision. Prepared launches never follow later request edits.
    """

    declaration: Experiment[..., _ExperimentResultT_co]
    values: dict[str, object]

    def copy(self) -> ExperimentRequest[_ExperimentResultT_co]:
        return ExperimentRequest(self.declaration, deepcopy(self.values))


__all__ = [
    "Experiment",
    "ExperimentInvocation",
    "ExperimentRequest",
    "Scan",
]
