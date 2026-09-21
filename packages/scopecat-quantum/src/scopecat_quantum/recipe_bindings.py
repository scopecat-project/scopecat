"""Demand-driven bindings between calibration sources and pulse functions."""

import inspect
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from functools import partial
from typing import get_type_hints

from scopecat_quantum._ids import CouplerId, GateId
from scopecat_quantum._recipe_identity import gate_implementation_id
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
from scopecat_quantum.pulse_implementations import (
    GatePulseImplementation,
    GatePulseImplementationKey,
    ResolvedPulseImplementations,
)
from scopecat_quantum.pulse_recipes import (
    PulseRecipeMaterializationCache,
)


@dataclass(frozen=True, slots=True)
class GateRecipeBinding[ParametersT]:
    """Bind a normal pulse function to one gate, independently of table schemas.

    The resolver runs only for matching calls, against their effective scope.
    Its named inputs are copied before use; callers need no mirror row dataclass.
    Gate-call arguments cannot be overwritten by calibration bindings. Functions
    and resolvers must be pure; replace the profile after changing their code.
    """

    id: str
    gate: GateDefinition
    build: Callable[..., QuantumFragment] = field(repr=False)
    inputs: Callable[[ParametersT, GateCall], Mapping[str, object]] = field(repr=False)
    resources: Callable[[ParametersT, GateCall], tuple[CouplerId, ...]] | None = field(
        default=None,
        repr=False,
    )
    _signature: inspect.Signature = field(init=False, repr=False)
    _resource_count: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("recipe binding id must be non-empty")
        signature = inspect.signature(self.build)
        parameters = tuple(signature.parameters.values())
        if any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in parameters):
            raise TypeError("pulse functions cannot use variadic parameters")
        positional = tuple(
            p
            for p in parameters
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
        hints = get_type_hints(self.build)
        if len(positional) < self.gate.qubit_arity or any(
            hints.get(p.name) is not Qubit for p in positional[: self.gate.qubit_arity]
        ):
            raise TypeError("pulse functions must start with the gate's Qubit operands")
        resources = positional[self.gate.qubit_arity :]
        if any(hints.get(p.name) is not Coupler for p in resources):
            raise TypeError("extra positional pulse inputs must be Coupler resources")
        keywords = {p.name for p in parameters if p.kind is p.KEYWORD_ONLY}
        if not {argument.id for argument in self.gate.parameters} <= keywords:
            raise TypeError("gate arguments must be named keyword-only pulse inputs")
        object.__setattr__(self, "_signature", signature)
        object.__setattr__(self, "_resource_count", len(resources))

    @property
    def recipe_ids(self) -> tuple[str, ...]:
        return (self.id,)

    def materialize(
        self,
        parameters: ParametersT,
        circuit: VerifiedCircuitOperations,
        *,
        cache: PulseRecipeMaterializationCache | None = None,
        scoped_parameters: Mapping[str, ParametersT] | None = None,
    ) -> ResolvedPulseImplementations:
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
        implementations: list[GatePulseImplementation] = []
        seen: set[GatePulseImplementationKey] = set()
        scopes: Mapping[str, ParametersT] = (
            {} if scoped_parameters is None else scoped_parameters
        )
        for call in operations:
            if not isinstance(call, GateCall) or call.gate_id != self.gate.id:
                continue
            if gate_definition(call.gate_id) != self.gate:
                raise ValueError(
                    f"recipe {self.id!r} conflicts with the gate definition"
                )
            key = GatePulseImplementationKey.from_call(call)
            if key in seen:
                continue
            if call.recipe_scope is None:
                selected = parameters
            else:
                if call.recipe_scope not in scopes:
                    raise ValueError(
                        f"recipe {self.id!r}: no parameters for scope "
                        f"{call.recipe_scope!r}"
                    )
                selected = scopes[call.recipe_scope]
            label = (
                f"recipe {self.id!r} for {tuple(q.value for q in call.qubits)!r}"
                f" scope {call.recipe_scope!r}"
            )
            try:
                inputs = deepcopy(dict(self.inputs(selected, call)))
                resources = (
                    () if self.resources is None else self.resources(selected, call)
                )
                arguments = {argument.id: argument.value for argument in call.arguments}
                overlap = inputs.keys() & arguments.keys()
                if overlap:
                    raise ValueError(
                        "calibration inputs cannot override gate arguments: "
                        f"{sorted(overlap)}"
                    )
                if len(resources) != self._resource_count:
                    raise ValueError(
                        f"expected {self._resource_count} coupler resources"
                    )
                operands = tuple(qubit(q.value) for q in call.qubits)
                handles = tuple(coupler(resource.value) for resource in resources)
                bound = self._signature.bind(*operands, *handles, **inputs, **arguments)
                bound.apply_defaults()
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{label}: input binding failed: {error}") from error
            build = partial(self._materialize, key, resources, bound)
            implementation = (
                build()
                if cache is None
                else cache.materialize_gate(
                    self.id,
                    inputs,
                    key,
                    resources,
                    build,
                )
            )
            implementations.append(implementation)
            seen.add(key)
        return ResolvedPulseImplementations(
            gates=tuple(implementations), measurements=()
        )

    def _materialize(
        self,
        key: GatePulseImplementationKey,
        resources: tuple[CouplerId, ...],
        bound: inspect.BoundArguments,
    ) -> GatePulseImplementation:
        identity = gate_implementation_id(self.id, key)
        body = self.build(*bound.args, **bound.kwargs)
        return GatePulseImplementation(
            id=identity,
            key=key,
            resources=resources,
            pulse_template=materialize_pulse_recipe_body(
                f"{identity.value}.template", body
            ),
        )


def bind_gate_pulse_recipe[ParametersT](
    *,
    of: Gate | GateDefinition,
    build: Callable[..., QuantumFragment],
    inputs: Callable[[ParametersT, GateCall], Mapping[str, object]],
    resources: Callable[[ParametersT, GateCall], tuple[CouplerId, ...]] | None = None,
    id: str | None = None,
) -> GateRecipeBinding[ParametersT]:
    """Bind named calibration inputs; no row model or global registration required."""
    gate = of if isinstance(of, GateDefinition) else of.definition
    identity = (
        f"{build.__module__}.{build.__qualname__}:{gate.id.value}" if id is None else id
    )
    return GateRecipeBinding(identity, gate, build, inputs, resources)
