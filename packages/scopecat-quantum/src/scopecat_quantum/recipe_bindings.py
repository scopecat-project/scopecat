"""Demand-driven bindings between calibration sources and pulse functions."""

import inspect
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from functools import partial
from typing import get_type_hints
from urllib.parse import quote

from scopecat.kernel.content_identity import content_fingerprint, stable_content_hash

from scopecat_quantum._ids import CouplerId, GateId, PulseImplementationId
from scopecat_quantum._recipe_identity import gate_implementation_id
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
    GatePulseImplementationKey,
    ResolvedPulseImplementations,
)
from scopecat_quantum.pulse_recipes import (
    PulseRecipeMaterializationCache,
)
from scopecat_quantum.recipe_evidence import RecipeInputEvidence, ResolvedRecipeInputs
from scopecat_quantum.recipe_queries import RecipeParameterInputs


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

    def declarative_inputs(
        self,
        *,
        gate_id: GateId | None = None,
        measurement_kind: AcquisitionKind | None = None,
    ) -> tuple[RecipeParameterInputs, ...]:
        del measurement_kind
        if gate_id == self.gate.id and isinstance(self.inputs, RecipeParameterInputs):
            return (self.inputs,)
        return ()

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
        evidence: list[RecipeInputEvidence] = []
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
                resolved_inputs = self.inputs(selected, call)
                inputs = deepcopy(dict(resolved_inputs))
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
            if isinstance(resolved_inputs, ResolvedRecipeInputs):
                evidence.append(
                    RecipeInputEvidence(
                        self.id,
                        implementation.id.value,
                        implementation.fingerprint,
                        call.recipe_scope,
                        resolved_inputs,
                    )
                )
            seen.add(key)
        return ResolvedPulseImplementations(
            gates=tuple(implementations),
            measurements=(),
            parameter_evidence=tuple(evidence),
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


@dataclass(frozen=True, slots=True)
class MeasurementRecipeBinding[ParametersT]:
    """Resolve named readout inputs only for used measurement contracts.

    Readout uses the effective baseline snapshot even within a gate recipe scope.
    Builders own pulse/acquisition timing; the framework preserves result identity.
    """

    id: str
    kind: AcquisitionKind
    build: Callable[..., QuantumFragment] = field(repr=False)
    inputs: Callable[[ParametersT, Measure], Mapping[str, object]] = field(repr=False)
    _signature: inspect.Signature = field(init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("recipe binding id must be non-empty")
        signature = inspect.signature(self.build)
        parameters = tuple(signature.parameters.values())
        positional = tuple(
            p
            for p in parameters
            if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
        )
        if (
            len(positional) != 1
            or get_type_hints(self.build).get(positional[0].name) is not Qubit
            or any(p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD) for p in parameters)
        ):
            raise TypeError(
                "measurement pulse functions require one Qubit and named inputs"
            )
        object.__setattr__(self, "_signature", signature)

    @property
    def recipe_ids(self) -> tuple[str, ...]:
        return (self.id,)

    def declarative_inputs(
        self,
        *,
        gate_id: GateId | None = None,
        measurement_kind: AcquisitionKind | None = None,
    ) -> tuple[RecipeParameterInputs, ...]:
        del gate_id
        if measurement_kind == self.kind and isinstance(
            self.inputs, RecipeParameterInputs
        ):
            return (self.inputs,)
        return ()

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
        # Measurements deliberately use baseline parameters, independent of gate scopes.
        del gate_definition, scoped_parameters
        implementations: list[MeasurementPulseImplementation] = []
        evidence: list[RecipeInputEvidence] = []
        seen: set[MeasurementPulseImplementationKey] = set()
        for measurement in operations:
            if (
                not isinstance(measurement, Measure)
                or measurement.contract.acquisition_kind is not self.kind
            ):
                continue
            key = MeasurementPulseImplementationKey.from_measurement(measurement)
            if key in seen:
                continue
            label = f"measurement recipe {self.id!r} for {measurement.qubit.value!r}"
            try:
                resolved_inputs = self.inputs(parameters, measurement)
                inputs = deepcopy(dict(resolved_inputs))
                bound = self._signature.bind(qubit(measurement.qubit.value), **inputs)
                bound.apply_defaults()
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError(f"{label}: input binding failed: {error}") from error
            build = partial(self._materialize, key, bound)
            implementation = (
                build()
                if cache is None
                else cache.materialize_measurement(
                    self.id,
                    inputs,
                    key,
                    build,
                )
            )
            implementations.append(implementation)
            if isinstance(resolved_inputs, ResolvedRecipeInputs):
                evidence.append(
                    RecipeInputEvidence(
                        self.id,
                        implementation.id.value,
                        implementation.fingerprint,
                        None,
                        resolved_inputs,
                    )
                )
            seen.add(key)
        return ResolvedPulseImplementations(
            gates=(),
            measurements=tuple(implementations),
            parameter_evidence=tuple(evidence),
        )

    def _materialize(
        self,
        key: MeasurementPulseImplementationKey,
        bound: inspect.BoundArguments,
    ) -> MeasurementPulseImplementation:
        contract_id = stable_content_hash(content_fingerprint(key.contract))
        identity = PulseImplementationId(
            f"{self.id}[{quote(key.qubit.value, safe='-._~')}][{contract_id}]"
        )
        body = self.build(*bound.args, **bound.kwargs)
        return MeasurementPulseImplementation(
            id=identity,
            key=key,
            pulse_template=materialize_pulse_recipe_body(
                f"{identity.value}.template",
                body,
                measurement=(key.qubit, key.contract),
            ),
        )


def bind_measurement_pulse_recipe[ParametersT](
    *,
    kind: AcquisitionKind,
    build: Callable[..., QuantumFragment],
    inputs: Callable[[ParametersT, Measure], Mapping[str, object]],
    id: str | None = None,
) -> MeasurementRecipeBinding[ParametersT]:
    """Bind a pulse/acquisition function without imposing a parameter row model."""
    identity = (
        f"{build.__module__}.{build.__qualname__}:{kind.value}" if id is None else id
    )
    return MeasurementRecipeBinding(identity, kind, build, inputs)
