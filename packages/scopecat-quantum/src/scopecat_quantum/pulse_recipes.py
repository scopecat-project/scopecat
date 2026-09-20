"""Declarative row maps for compiler-owned pulse implementations."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING, Concatenate, Protocol, cast, get_type_hints
from urllib.parse import quote

from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash

from scopecat_quantum._ids import CouplerId, GateId, PulseImplementationId, QubitId
from scopecat_quantum.acquisitions import AcquisitionKind
from scopecat_quantum.authoring import (
    Coupler,
    Gate,
    QuantumFragment,
    Qubit,
    coupler,
    materialize_pulse_recipe_body,
    qubit,
)
from scopecat_quantum.circuits import Measure, VerifiedCircuitOperations
from scopecat_quantum.gates import GateCall, GateDefinition
from scopecat_quantum.measurement_implementations import (
    MeasurementPulseImplementation,
    MeasurementPulseImplementationKey,
)
from scopecat_quantum.pulse_implementations import (
    GatePulseImplementation,
    GatePulseImplementationArgument,
    GatePulseImplementationKey,
    ResolvedPulseImplementations,
)

if TYPE_CHECKING:
    from scopecat_quantum.programs import VerifiedQuantumProgram

type _GateRecipeTarget = GateDefinition | Gate
type _GateRecipeCacheKey = tuple[
    str,
    str,
    GatePulseImplementationKey,
    tuple[CouplerId, ...],
]
type _MeasurementRecipeCacheKey = tuple[
    str,
    str,
    MeasurementPulseImplementationKey,
]


@dataclass(slots=True)
class PulseRecipeMaterializationCache:
    """Exact pulse implementations retained for one compilation request.

    A cache binds to the first profile that uses it and rejects reuse by another
    profile. Row fingerprints keep point-dependent calibration values in the key;
    object-identity memoization only avoids re-fingerprinting the same immutable
    compiler-parameter row within the request.
    """

    _profile_token: object | None = field(default=None, init=False, repr=False)
    _row_fingerprints: dict[int, tuple[object, str]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _gates: dict[_GateRecipeCacheKey, GatePulseImplementation] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _measurements: dict[
        _MeasurementRecipeCacheKey,
        MeasurementPulseImplementation,
    ] = field(default_factory=dict, init=False, repr=False)

    def bind(self, profile_token: object) -> None:
        if self._profile_token is None:
            self._profile_token = profile_token
        elif self._profile_token is not profile_token:
            raise ValueError("pulse recipe cache cannot be shared across profiles")

    def _row_fingerprint(self, row: object) -> str:
        identity = id(row)
        retained = self._row_fingerprints.get(identity)
        if retained is not None and retained[0] is row:
            return retained[1]
        fingerprint = stable_content_hash(content_fingerprint(row))
        self._row_fingerprints[identity] = (row, fingerprint)
        return fingerprint

    def materialize_gate(
        self,
        recipe_id: str,
        row: object,
        key: GatePulseImplementationKey,
        resources: tuple[CouplerId, ...],
        build: Callable[[], GatePulseImplementation],
    ) -> GatePulseImplementation:
        """Return or create one exact gate implementation."""

        cache_key = (recipe_id, self._row_fingerprint(row), key, resources)
        implementation = self._gates.get(cache_key)
        if implementation is None:
            implementation = build()
            self._gates[cache_key] = implementation
        return implementation

    def materialize_measurement(
        self,
        recipe_id: str,
        row: object,
        key: MeasurementPulseImplementationKey,
        build: Callable[[], MeasurementPulseImplementation],
    ) -> MeasurementPulseImplementation:
        """Return or create one exact measurement implementation."""

        cache_key = (recipe_id, self._row_fingerprint(row), key)
        implementation = self._measurements.get(cache_key)
        if implementation is None:
            implementation = build()
            self._measurements[cache_key] = implementation
        return implementation


def _gate_definition(target: _GateRecipeTarget) -> GateDefinition:
    return target if isinstance(target, GateDefinition) else target.definition


def _encoded_operands(operands: tuple[QubitId, ...]) -> str:
    return ",".join(quote(operand.value, safe="-._~") for operand in operands)


def _gate_implementation_id(
    recipe_id: str,
    key: GatePulseImplementationKey,
) -> PulseImplementationId:
    suffix = f"[{_encoded_operands(key.operands)}]"
    if key.arguments:
        argument_hash = stable_content_hash(content_fingerprint(key.arguments))
        suffix = f"{suffix}[{argument_hash}]"
    if key.recipe_scope is not None:
        suffix = f"{suffix}[scope={quote(key.recipe_scope, safe='-._~')}]"
    return PulseImplementationId(f"{recipe_id}{suffix}")


def _gate_recipe_resource_count(
    build: Callable[..., QuantumFragment],
    gate: GateDefinition,
) -> int:
    """Validate the row-first authoring signature and return its resource arity."""

    signature = inspect.signature(build)
    parameters = tuple(signature.parameters.values())
    if any(
        parameter.kind
        in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD)
        for parameter in parameters
    ):
        raise TypeError("gate pulse recipe signatures cannot use variadic parameters")
    positional = tuple(
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    if len(positional) < gate.qubit_arity + 1:
        raise TypeError(
            "gate pulse recipes require a leading row followed by every gate qubit"
        )
    hints = get_type_hints(build)
    operand_parameters = positional[1 : gate.qubit_arity + 1]
    if any(hints.get(parameter.name) is not Qubit for parameter in operand_parameters):
        raise TypeError("gate pulse recipe operands must be annotated as Qubit")
    resource_parameters = positional[gate.qubit_arity + 1 :]
    if any(
        hints.get(parameter.name) is not Coupler for parameter in resource_parameters
    ):
        raise TypeError("gate pulse recipe resources must be annotated as Coupler")

    keyword_only = tuple(
        parameter
        for parameter in parameters
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    )
    expected_arguments = tuple(parameter.id for parameter in gate.parameters)
    if tuple(parameter.name for parameter in keyword_only) != expected_arguments:
        rendered = ", ".join(repr(item) for item in expected_arguments) or "none"
        raise TypeError(
            "gate pulse recipe keyword-only parameters must exactly match "
            f"gate arguments in definition order: {rendered}"
        )
    return len(resource_parameters)


def _validate_measurement_recipe_signature(
    build: Callable[..., QuantumFragment],
) -> None:
    signature = inspect.signature(build)
    parameters = tuple(signature.parameters.values())
    positional = tuple(
        parameter
        for parameter in parameters
        if parameter.kind
        in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    )
    if len(parameters) != 2 or len(positional) != 2:
        raise TypeError("measurement pulse recipes require exactly a row and one qubit")
    hints = get_type_hints(build)
    if hints.get(positional[1].name) is not Qubit:
        raise TypeError("measurement pulse recipe operand must be annotated as Qubit")


@dataclass(frozen=True, slots=True)
class GatePulseRecipe[RowT]:
    """A row-mapped gate recipe authored with concrete logical handles.

    Recipe functions receive the selected row first, followed by gate qubits,
    declared couplers, and the actual gate-call arguments as keyword-only
    values. They return authoring fragments; local pulse identities are owned
    by the materializer.
    """

    id: str
    gate: GateDefinition
    build: Callable[..., QuantumFragment] = field(repr=False, compare=False)
    _resource_count: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("gate pulse recipe id must be non-empty")
        object.__setattr__(
            self,
            "_resource_count",
            _gate_recipe_resource_count(self.build, self.gate),
        )

    def implementation_id(
        self,
        operands: tuple[QubitId, ...],
        arguments: tuple[GatePulseImplementationArgument, ...] = (),
        *,
        recipe_scope: str | None = None,
    ) -> PulseImplementationId:
        """Derive the stable implementation identity for one exact call key."""

        return _gate_implementation_id(
            self.id,
            GatePulseImplementationKey(
                gate_id=self.gate.id,
                operands=operands,
                arguments=arguments,
                recipe_scope=recipe_scope,
            ),
        )

    def materialize(
        self,
        row: RowT,
        call: GateCall,
        resources: tuple[CouplerId, ...] = (),
    ) -> GatePulseImplementation:
        """Instantiate the recipe for one operation and its mapped resources."""

        if call.gate_id != self.gate.id:
            raise ValueError("gate pulse recipe call must match its gate definition")
        if len(resources) != self._resource_count:
            raise ValueError(
                f"gate pulse recipe {self.id!r} requires "
                f"{self._resource_count} coupler resources"
            )
        key = GatePulseImplementationKey.from_call(call)
        implementation_id = _gate_implementation_id(self.id, key)
        arguments = {argument.id: argument.value for argument in call.arguments}
        body = self.build(
            row,
            *(qubit(operand.value) for operand in call.qubits),
            *(coupler(resource.value) for resource in resources),
            **arguments,
        )
        return GatePulseImplementation(
            id=implementation_id,
            key=key,
            pulse_template=materialize_pulse_recipe_body(
                f"{implementation_id.value}.template",
                body,
            ),
            resources=resources,
        )


@dataclass(frozen=True, slots=True)
class MeasurementPulseRecipe[RowT]:
    """A row-mapped single-qubit measurement recipe."""

    id: str
    acquisition_kind: AcquisitionKind
    build: Callable[[RowT, Qubit], QuantumFragment] = field(
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("measurement pulse recipe id must be non-empty")
        _validate_measurement_recipe_signature(self.build)

    def implementation_id(self, qubit_id: QubitId) -> PulseImplementationId:
        """Derive the stable implementation identity for one logical qubit."""

        return PulseImplementationId(f"{self.id}[{quote(qubit_id.value, safe='-._~')}]")

    def materialize(
        self,
        row: RowT,
        measurement: Measure,
    ) -> MeasurementPulseImplementation:
        """Instantiate the recipe for one logical measurement shape."""

        if measurement.contract.acquisition_kind is not self.acquisition_kind:
            raise ValueError("measurement pulse recipe kind must match its operation")
        implementation_id = self.implementation_id(measurement.qubit)
        target = qubit(measurement.qubit.value)
        body = self.build(row, target)
        return MeasurementPulseImplementation(
            id=implementation_id,
            key=MeasurementPulseImplementationKey.from_measurement(measurement),
            pulse_template=materialize_pulse_recipe_body(
                f"{implementation_id.value}.template",
                body,
                measurement=(measurement.qubit, measurement.contract),
            ),
        )


@dataclass(frozen=True, slots=True)
class _GatePulseRecipeDecorator:
    id: str
    gate: GateDefinition

    def __call__[RowT, **P](
        self,
        build: Callable[Concatenate[RowT, P], QuantumFragment],
    ) -> GatePulseRecipe[RowT]:
        return GatePulseRecipe(
            id=self.id,
            gate=self.gate,
            build=cast("Callable[..., QuantumFragment]", build),
        )


def gate_pulse_recipe(
    *,
    of: _GateRecipeTarget,
    id: str,
) -> _GatePulseRecipeDecorator:
    """Declare a compiler-owned gate recipe without global registration."""

    return _GatePulseRecipeDecorator(id=id, gate=_gate_definition(of))


@dataclass(frozen=True, slots=True)
class _MeasurementPulseRecipeDecorator:
    id: str
    acquisition_kind: AcquisitionKind

    def __call__[RowT](
        self,
        build: Callable[[RowT, Qubit], QuantumFragment],
    ) -> MeasurementPulseRecipe[RowT]:
        return MeasurementPulseRecipe(
            id=self.id,
            acquisition_kind=self.acquisition_kind,
            build=build,
        )


def measurement_pulse_recipe(
    *,
    kind: AcquisitionKind,
    id: str,
) -> _MeasurementPulseRecipeDecorator:
    """Declare a compiler-owned measurement recipe without global registration."""

    return _MeasurementPulseRecipeDecorator(
        id=id,
        acquisition_kind=kind,
    )


@dataclass(frozen=True, slots=True)
class PulseRecipeMap[ParametersT, RowT]:
    """Join logical operations to one homogeneous compiler-parameter collection."""

    rows: Callable[[ParametersT], Iterable[RowT]] = field(repr=False, compare=False)
    operands: Callable[[RowT], tuple[QubitId, ...]] = field(
        repr=False,
        compare=False,
    )
    resources: Callable[[RowT], tuple[CouplerId, ...]] | None = field(
        default=None,
        repr=False,
        compare=False,
    )
    gates: tuple[GatePulseRecipe[RowT], ...] = ()
    measurements: tuple[MeasurementPulseRecipe[RowT], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "gates", tuple(self.gates))
        object.__setattr__(self, "measurements", tuple(self.measurements))

    @property
    def recipe_ids(self) -> tuple[str, ...]:
        return tuple(recipe.id for recipe in (*self.gates, *self.measurements))

    def materialize(
        self,
        parameters: ParametersT,
        circuit: VerifiedCircuitOperations,
        *,
        cache: PulseRecipeMaterializationCache | None = None,
        scoped_parameters: Mapping[str, ParametersT] | None = None,
    ) -> ResolvedPulseImplementations:
        """Map only operations present in the bound circuit onto matching rows."""

        return self.materialize_operations(
            parameters,
            circuit.operations,
            gate_definition=circuit.gate_definition,
            cache=cache,
            scoped_parameters=scoped_parameters,
        )

    def materialize_operations(
        self,
        parameters: ParametersT,
        operations: Iterable[GateCall | Measure],
        *,
        gate_definition: Callable[[GateId], GateDefinition],
        cache: PulseRecipeMaterializationCache | None = None,
        scoped_parameters: Mapping[str, ParametersT] | None = None,
    ) -> ResolvedPulseImplementations:
        """Join a streamed concrete operation sequence without retaining it."""

        scopes: Mapping[str, ParametersT] = (
            {} if scoped_parameters is None else scoped_parameters
        )
        rows_by_scope: dict[
            str | None, dict[tuple[QubitId, ...], tuple[RowT, tuple[CouplerId, ...]]]
        ] = {}

        def rows_for(
            scope: str | None,
        ) -> dict[tuple[QubitId, ...], tuple[RowT, tuple[CouplerId, ...]]]:
            if scope in rows_by_scope:
                return rows_by_scope[scope]
            if scope is not None and scope not in scopes:
                raise ValueError(f"no pulse recipe parameters for scope {scope!r}")
            selected = parameters if scope is None else scopes[scope]
            mapped: dict[tuple[QubitId, ...], tuple[RowT, tuple[CouplerId, ...]]] = {}
            for row in self.rows(selected):
                operands = tuple(self.operands(row))
                if operands in mapped:
                    raise ValueError("pulse recipe map operands must be unique")
                if self.measurements and len(operands) != 1:
                    raise ValueError(
                        "measurement pulse recipe maps require one qubit operand"
                    )
                resources = () if self.resources is None else tuple(self.resources(row))
                mapped[operands] = (row, resources)
            rows_by_scope[scope] = mapped
            return mapped

        gates: list[GatePulseImplementation] = []
        measurements: list[MeasurementPulseImplementation] = []
        materialized_gates: set[tuple[str, GatePulseImplementationKey]] = set()
        materialized_measurements: set[
            tuple[str, MeasurementPulseImplementationKey]
        ] = set()
        for operation in operations:
            operands = (
                operation.qubits
                if isinstance(operation, GateCall)
                else (operation.qubit,)
            )
            scope = operation.recipe_scope if isinstance(operation, GateCall) else None
            mapped = rows_for(scope).get(operands)
            if mapped is None:
                continue
            row, resources = mapped
            if isinstance(operation, GateCall):
                for recipe in self.gates:
                    if recipe.gate.id != operation.gate_id:
                        continue
                    if gate_definition(operation.gate_id) != recipe.gate:
                        raise ValueError(
                            f"pulse recipe {recipe.id!r} conflicts with the "
                            "bound circuit gate definition"
                        )
                    key = GatePulseImplementationKey.from_call(operation)
                    marker = (recipe.id, key)
                    if marker not in materialized_gates:
                        if cache is None:
                            implementation = recipe.materialize(
                                row,
                                operation,
                                resources,
                            )
                        else:
                            implementation = cache.materialize_gate(
                                recipe.id,
                                row,
                                key,
                                resources,
                                partial(recipe.materialize, row, operation, resources),
                            )
                        gates.append(implementation)
                        materialized_gates.add(marker)
                continue
            for recipe in self.measurements:
                if recipe.acquisition_kind is not operation.contract.acquisition_kind:
                    continue
                key = MeasurementPulseImplementationKey.from_measurement(operation)
                marker = (recipe.id, key)
                if marker not in materialized_measurements:
                    if cache is None:
                        implementation = recipe.materialize(row, operation)
                    else:
                        implementation = cache.materialize_measurement(
                            recipe.id,
                            row,
                            key,
                            partial(recipe.materialize, row, operation),
                        )
                    measurements.append(implementation)
                    materialized_measurements.add(marker)
        return ResolvedPulseImplementations(
            gates=tuple(gates),
            measurements=tuple(measurements),
        )


def map_qubit_pulse_recipes[ParametersT, RowT](
    *,
    rows: Callable[[ParametersT], Iterable[RowT]],
    qubit: Callable[[RowT], QubitId],
    resources: Callable[[RowT], tuple[CouplerId, ...]] | None = None,
    gates: Iterable[GatePulseRecipe[RowT]] = (),
    measurements: Iterable[MeasurementPulseRecipe[RowT]] = (),
) -> PulseRecipeMap[ParametersT, RowT]:
    """Build the common one-qubit row map with one entity selector."""

    return PulseRecipeMap(
        rows=rows,
        operands=lambda row: (qubit(row),),
        resources=resources,
        gates=tuple(gates),
        measurements=tuple(measurements),
    )


class _PulseRecipeMapping[ParametersT](Protocol):
    @property
    def recipe_ids(self) -> tuple[str, ...]: ...

    def materialize(
        self,
        parameters: ParametersT,
        circuit: VerifiedCircuitOperations,
        *,
        cache: PulseRecipeMaterializationCache | None = None,
        scoped_parameters: Mapping[str, ParametersT] | None = None,
    ) -> ResolvedPulseImplementations: ...

    def materialize_operations(
        self,
        parameters: ParametersT,
        operations: Iterable[GateCall | Measure],
        *,
        gate_definition: Callable[[GateId], GateDefinition],
        cache: PulseRecipeMaterializationCache | None = None,
        scoped_parameters: Mapping[str, ParametersT] | None = None,
    ) -> ResolvedPulseImplementations: ...


@dataclass(frozen=True, slots=True, init=False)
class PulseRecipeProfile[ParametersT]:
    """Static recipes joined only to operations used by the point-bound circuit."""

    _mappings: tuple[_PulseRecipeMapping[ParametersT], ...]
    _cache_token: object

    def __init__(self, *mappings: _PulseRecipeMapping[ParametersT]) -> None:
        recipe_ids = tuple(
            recipe_id for mapping in mappings for recipe_id in mapping.recipe_ids
        )
        if len(set(recipe_ids)) != len(recipe_ids):
            raise ValueError("pulse recipe ids must be unique within one profile")
        object.__setattr__(self, "_mappings", tuple(mappings))
        object.__setattr__(self, "_cache_token", object())

    def materialize(
        self,
        parameters: ParametersT,
        circuit: VerifiedCircuitOperations,
        *,
        cache: PulseRecipeMaterializationCache | None = None,
        scoped_parameters: Mapping[str, ParametersT] | None = None,
    ) -> ResolvedPulseImplementations:
        """Join every configured row map to one point-bound circuit."""

        if cache is not None:
            cache.bind(self._cache_token)
        resolved = tuple(
            mapping.materialize(
                parameters, circuit, cache=cache, scoped_parameters=scoped_parameters
            )
            for mapping in self._mappings
        )
        return ResolvedPulseImplementations(
            gates=tuple(
                implementation
                for mapping in resolved
                for implementation in mapping.gates
            ),
            measurements=tuple(
                implementation
                for mapping in resolved
                for implementation in mapping.measurements
            ),
        )

    def materialize_quantum(
        self,
        parameters: ParametersT,
        program: VerifiedQuantumProgram,
        *,
        max_expanded_operations: int | None = None,
        cache: PulseRecipeMaterializationCache | None = None,
        scoped_parameters: Mapping[str, ParametersT] | None = None,
    ) -> ResolvedPulseImplementations:
        """Join recipes to retained Map/Repeat leaves as a stream."""

        program.require_expansion_budget(max_expanded_operations)
        if cache is not None:
            cache.bind(self._cache_token)
        resolved = tuple(
            mapping.materialize_operations(
                parameters,
                program.iter_expanded_unresolved_operations(),
                gate_definition=program.unresolved.gate_definition,
                cache=cache,
                scoped_parameters=scoped_parameters,
            )
            for mapping in self._mappings
        )
        return ResolvedPulseImplementations(
            gates=tuple(
                implementation
                for mapping in resolved
                for implementation in mapping.gates
            ),
            measurements=tuple(
                implementation
                for mapping in resolved
                for implementation in mapping.measurements
            ),
        )


__all__ = [
    "GatePulseRecipe",
    "MeasurementPulseRecipe",
    "PulseRecipeMap",
    "PulseRecipeMaterializationCache",
    "PulseRecipeProfile",
    "gate_pulse_recipe",
    "map_qubit_pulse_recipes",
    "measurement_pulse_recipe",
]
