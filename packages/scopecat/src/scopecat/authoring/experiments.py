"""Callable experiment requests and explicit immutable program construction."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field, fields, is_dataclass, replace
from types import MappingProxyType
from typing import Generic, Literal, ParamSpec, SupportsFloat, TypeVar, cast

from scopecat.authoring.parameter_models import (
    ParameterFieldIdentity,
    parameter_cell_key,
    parameter_definition,
    parameter_table_name,
)
from scopecat.kernel.python_source import python_source_identity
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Scalar
from scopecat.kernel.value_validation import coerce_literal
from scopecat.program.controls import Control, ControlSet
from scopecat.program.definitions import ExperimentInvocation
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.parameter import ParameterAtomValue
from scopecat.records.request_sweep import ParameterSweep

type ExperimentBuilder[ResultT] = Callable[
    [Mapping[str, object]], ExperimentInvocation[ResultT]
]

_P = ParamSpec("_P")
_ValuesT = TypeVar("_ValuesT", covariant=True, default=dict[str, object])
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
    control: Control | None = None

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
    code_revision: AuthorRevisionRef | None = field(default=None, repr=False)
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

    def build(
        self,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> ExperimentInvocation[_ExperimentResultT_co]:
        """Build one immutable invocation from structural and runtime arguments."""

        bound = self._signature.bind(*args, **kwargs)
        return self._builder(cast("Mapping[str, object]", bound.arguments))

    def __call__(
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
            if control.ownership == "editable" and control.default is not None:
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
class ExperimentRequest(Generic[_ExperimentResultT_co, _ValuesT]):
    """Editable values tied to a declaration; contains no cached program or output.

    Mutate ``values`` or make an isolated ``copy()`` before exploring alternatives.
    ``author.prepare(request)`` captures the values and rebuilds on the selected
    managed source revision. Prepared launches never follow later request edits.
    """

    declaration: Experiment[..., _ExperimentResultT_co]
    values: _ValuesT
    scan_mode: Literal["cartesian", "paired"] = "cartesian"
    parameter_sweeps: tuple[ParameterSweep, ...] = ()

    def copy(self) -> ExperimentRequest[_ExperimentResultT_co, _ValuesT]:
        return replace(
            self,
            values=deepcopy(self.values),
            parameter_sweeps=tuple(
                s.model_copy(deep=True) for s in self.parameter_sweeps
            ),
        )

    def sweep(
        self,
        *,
        mode: Literal["cartesian", "paired"] = "cartesian",
        **axes: Iterable[SupportsFloat | Quantity],
    ) -> ExperimentRequest[_ExperimentResultT_co, dict[str, object]]:
        """Copy with explicit outer scans; local device arrays stay arrays."""
        values = self.snapshot()
        controls = {control.id: control for control in self.declaration.controls.fields}
        for name, items in axes.items():
            if name not in controls or not controls[name].scannable:
                raise ValueError(f"{name!r} is not a scannable experiment input")
            values[name] = Scan(items)
        return ExperimentRequest(self.declaration, values, mode, self.parameter_sweeps)

    def sweep_parameter(
        self,
        field: ParameterFieldIdentity,
        key: ParameterAtomValue | tuple[ParameterAtomValue, ...],
        values: Iterable[SupportsFloat | Quantity],
        *,
        name: str,
    ) -> ExperimentRequest[_ExperimentResultT_co, _ValuesT]:
        """Overlay one typed cell per point without editing saved parameters."""
        value_type = parameter_definition(field).value_type
        if not isinstance(value_type, Scalar):
            raise TypeError("parameter sweep requires a scalar column")
        selected = tuple(
            cast("float | Quantity", coerce_literal(value_type, value, path=(name,)))
            for value in Scan(values).values
        )
        if not selected:
            raise ValueError("parameter sweep requires at least one value")
        sweep = ParameterSweep(
            name=name,
            table=parameter_table_name(field.owner),
            column=field.name,
            key=parameter_cell_key(field, key),
            value_type=value_type,
            values=selected,
        )
        if any(
            item.name == name
            or (item.table, item.column, item.key)
            == (sweep.table, sweep.column, sweep.key)
            for item in self.parameter_sweeps
        ):
            raise ValueError("parameter sweep name or target already selected")
        return replace(self.copy(), parameter_sweeps=(*self.parameter_sweeps, sweep))

    def snapshot(self) -> dict[str, object]:
        """Capture plain input values, preserving Quantity and Scan objects."""
        if is_dataclass(self.values) and not isinstance(self.values, type):
            values = {
                item.name: getattr(self.values, item.name)
                for item in fields(self.values)
            }
        else:
            values = cast("dict[str, object]", self.values)
        return deepcopy(values)

    def typed[ValuesT](
        self, values_type: type[ValuesT]
    ) -> ExperimentRequest[_ExperimentResultT_co, ValuesT]:
        """Copy into an explicit dataclass for statically checked field editing.

        The dataclass names must cover the current values exactly. Its defaults
        never replace the declaration's selected values. Preparation still uses
        the original experiment contract, including controls and source identity.
        """
        if not is_dataclass(values_type):
            raise TypeError("typed request values require a dataclass type")
        values = self.snapshot()
        names = {item.name for item in fields(values_type)}
        if names != values.keys():
            missing = sorted(values.keys() - names)
            extra = sorted(names - values.keys())
            raise ValueError(
                "typed request fields must match values: "
                f"missing={missing}, extra={extra}"
            )
        return ExperimentRequest(
            self.declaration,
            values_type(**values),
            self.scan_mode,
            self.parameter_sweeps,
        )


__all__ = [
    "Experiment",
    "ExperimentInvocation",
    "ExperimentRequest",
    "Scan",
]
