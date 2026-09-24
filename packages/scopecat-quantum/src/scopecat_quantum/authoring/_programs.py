# pyright: reportPrivateUsage=false
"""Closed quantum programs and core domain integration."""

from __future__ import annotations

import inspect
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from scopecat.authoring import (
    ComputeInput,
    IntType,
    ScalarType,
    ValueRef,
    ValueType,
)
from scopecat.authoring.entity_selection import PerEntity
from scopecat.authoring.occurrence_names import domain_occurrence_name
from scopecat.domain.program import DomainProgramDef
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_type_compatibility import (
    literal_scalar_type,
    require_assignable,
)
from scopecat.kernel.value_validation import coerce_literal
from scopecat.program.domain import (
    DomainCall,
    create_domain_call_internal,
)
from scopecat.program.domain import (
    domain_program as _core_domain_program,
)
from scopecat.program.identities import DomainCallKey
from scopecat.program.products import (
    ModuleProductDecl,
    ProductRef,
    ProductRefs,
    ProductValueSpec,
    entity_axis,
    product_axis,
    shot_axis,
)
from scopecat.program.value_refs import internal_literal_value_ref

from scopecat_quantum._ids import (
    QuantumProgramId,
)
from scopecat_quantum.acquisitions import QuantumResultDimension
from scopecat_quantum.recipe_parameters import (
    RecipeParameter,
    RecipeParameterBinding,
    recipe_parameter_input_ids,
)
from scopecat_quantum.recipe_selection import RecipeSelection, SelectableRecipes

from ._analysis import (
    _summarize_fragment,
    program_port_type,
)
from ._inspection import (
    describe,
    draw,
)
from ._ir import (
    QUANTUM_PROGRAM_DIALECT_ID,
    QUANTUM_PROGRAM_DIALECT_VERSION,
    CouplerSet,
    EntitySetPort,
    ProgramFunction,
    ProgramInput,
    ProgramPort,
    ProgramResults,
    PulseElement,
    QuantumFragment,
    QubitPairSet,
    QubitSet,
)
from ._selection import (
    CouplerSelectionIntent,
    QubitPairSelectionIntent,
    QubitSelectionIntent,
    coupler_selection_value_ref,
    qubit_pair_selection_value_ref,
    qubit_selection_value_ref,
)


class _ProgramFunctionContract(Protocol):
    @property
    def signature(self) -> inspect.Signature: ...


@dataclass(frozen=True, slots=True, repr=False)
class QuantumProgramCall:
    """One program invocation with automatically owned result products."""

    program: Program
    domain_call: DomainCall
    arguments: tuple[tuple[str, object], ...]
    compiler_arguments: tuple[tuple[str, ValueRef], ...]
    shots: ComputeInput

    @property
    def results(self) -> ProductRefs:
        """Return products owned by this native domain occurrence."""

        return self.domain_call.results

    def with_shots(self, shots: ComputeInput, /) -> QuantumProgramCall:
        """Return the same program call with a different acquisition count."""

        return _program_call(
            self.program,
            self.domain_call.id,
            inputs=dict(self.arguments),
            compiler_inputs=dict(self.compiler_arguments),
            shots=shots,
            key=self.domain_call.key,
        )

    def with_recipes(self, recipes: SelectableRecipes, /) -> QuantumProgramCall:
        """Select pulse implementations for this call, without copying parameters."""
        selected = Program(
            ir_id=self.program.ir_id,
            body=self.program.body,
            elements=self.program.elements,
            entity_sets=self.program.entity_sets,
            inputs=self.program.inputs,
            results=self.program.results,
            description=self.program.description,
            recipe_parameter_bindings=self.program.recipe_parameter_bindings,
            recipes=RecipeSelection(recipes),
        )
        return _program_call(
            selected,
            self.domain_call.id,
            inputs=dict(self.arguments),
            compiler_inputs=dict(self.compiler_arguments),
            shots=self.shots,
            key=self.domain_call.key,
        )

    def with_compiler_inputs(self, **inputs: ComputeInput) -> QuantumProgramCall:
        """Bind typed lowering-only values without changing the Program ABI."""

        recipe_ids = recipe_parameter_input_ids(self.program)
        if recipe_ids & inputs.keys():
            raise ValueError(
                "recipe parameter inputs are owned by with_recipe_parameters"
            )
        compiler_inputs = {
            name: value for name, value in self.compiler_arguments if name in recipe_ids
        } | dict(inputs)
        return _program_call(
            self.program,
            self.domain_call.id,
            inputs=dict(self.arguments),
            compiler_inputs=compiler_inputs,
            shots=self.shots,
            key=self.domain_call.key,
        )

    def with_recipe_parameters(
        self,
        scope: str,
        *parameters: RecipeParameter,
    ) -> QuantumProgramCall:
        """Bind point-local edits to gates inside the named recipe scope."""
        if not scope.strip():
            raise ValueError("recipe parameter scope must be non-empty")
        bindings = list(self.program.recipe_parameter_bindings)
        inputs = dict(self.compiler_arguments)
        for parameter in parameters:
            if any(
                (item.scope, item.table, item.column, item.key)
                == (scope, parameter.table, parameter.column, parameter.key)
                for item in bindings
            ):
                raise ValueError("duplicate recipe parameter cell in scope " + scope)
            input_id = f"__recipe_parameter_{len(bindings)}"
            if input_id in inputs:
                raise ValueError("compiler input uses reserved recipe parameter name")
            bindings.append(
                RecipeParameterBinding(
                    scope,
                    parameter.table,
                    parameter.column,
                    parameter.key,
                    parameter.value_type,
                    input_id,
                )
            )
            inputs[input_id] = _normalize_compiler_input(input_id, parameter.value)
        # ProgramDefinition has a custom constructor; copy only its closed IR.
        program = Program(
            ir_id=self.program.ir_id,
            body=self.program.body,
            elements=self.program.elements,
            entity_sets=self.program.entity_sets,
            inputs=self.program.inputs,
            results=self.program.results,
            description=self.program.description,
            recipe_parameter_bindings=tuple(bindings),
            recipes=self.program.recipes,
        )
        return _program_call(
            program,
            self.domain_call.id,
            inputs=dict(self.arguments),
            compiler_inputs=inputs,
            shots=self.shots,
            key=self.domain_call.key,
        )

    def entity_results(self) -> PerEntity[ProductRef]:
        """Return one result per concrete qubit for entity-axis recording.

        This is the common parallel-readout view. Programs that emit multiple
        results for one qubit must keep their named result structure instead.
        """

        arguments = dict(self.arguments)
        selected: list[tuple[EntityRef, ProductRef]] = []
        for result in self.program.results:
            bound_qubit = arguments[result.qubit.id]
            if isinstance(bound_qubit, EntityRef):
                entity = EntityRef(
                    id=bound_qubit.id,
                    kind=bound_qubit.kind or "logical_qubit",
                    metadata=bound_qubit.metadata,
                )
            elif isinstance(bound_qubit, str):
                entity = EntityRef(id=bound_qubit, kind="logical_qubit")
            else:
                raise TypeError(
                    "entity_results requires concrete string or EntityRef qubit inputs"
                )
            selected.append((entity, self.results[result.id]))
        if len({(entity.kind, entity.id) for entity, _product in selected}) != len(
            selected
        ):
            raise ValueError(
                "entity_results requires exactly one program result per qubit"
            )
        return PerEntity(selected)


@dataclass(frozen=True, slots=True, repr=False)
class Program:
    """A closed symbolic program containing logical and physical statements."""

    ir_id: QuantumProgramId
    body: QuantumFragment
    elements: tuple[PulseElement, ...]
    entity_sets: tuple[EntitySetPort, ...]
    inputs: tuple[ProgramInput, ...]
    results: ProgramResults
    description: str | None = None
    recipe_parameter_bindings: tuple[RecipeParameterBinding, ...] = ()
    recipes: RecipeSelection | None = None

    @property
    def id(self) -> str:
        """Return the stable program identity."""

        return self.ir_id.value

    @property
    def ports(self) -> tuple[ProgramPort, ...]:
        """Return bindable logical elements followed by scalar inputs."""

        return (*self.elements, *self.entity_sets, *self.inputs)

    def describe(self) -> str:
        """Describe the program's typed ports and result contracts as text."""

        return describe(self)

    def draw(self) -> str:
        """Draw the program's recursive source structure as a text tree."""

        return draw(self)


class ProgramDefinition(Program):
    """A function-authored program with an inspectable call signature."""

    __slots__ = ("_contract", "_definition")

    _contract: _ProgramFunctionContract
    _definition: ProgramFunction

    def __init__(
        self,
        declaration: Program,
        definition: ProgramFunction,
        contract: _ProgramFunctionContract,
    ) -> None:
        super().__init__(
            ir_id=declaration.ir_id,
            body=declaration.body,
            elements=declaration.elements,
            entity_sets=declaration.entity_sets,
            inputs=declaration.inputs,
            results=declaration.results,
            description=declaration.description,
            recipe_parameter_bindings=declaration.recipe_parameter_bindings,
            recipes=declaration.recipes,
        )
        self._definition = definition
        self._contract = contract

    @property
    def __wrapped__(self) -> ProgramFunction:
        return self._definition

    @property
    def __name__(self) -> str:
        return self._definition.__name__

    @property
    def __signature__(self) -> inspect.Signature:
        return self._contract.signature.replace(
            parameters=tuple(
                parameter.replace(annotation=ComputeInput)
                for parameter in self._contract.signature.parameters.values()
            ),
            return_annotation=QuantumProgramCall,
        )

    def __call__(
        self,
        *args: object,
        **inputs: object,
    ) -> QuantumProgramCall:
        """Bind ports, allocating an occurrence name in the current definition."""

        bound = self._contract.signature.bind(*args, **inputs)
        return _program_call(
            self,
            domain_occurrence_name(self.id.rsplit(".", maxsplit=1)[-1]),
            inputs=bound.arguments,
            compiler_inputs={},
            shots=1,
        )

    def call(
        self,
        instance_id: str,
        /,
        *args: object,
        **inputs: object,
    ) -> QuantumProgramCall:
        """Bind an explicitly named call in declared port order."""

        bound = self._contract.signature.bind(*args, **inputs)
        return _program_call(
            self,
            domain_occurrence_name(instance_id, explicit=True),
            inputs=bound.arguments,
            compiler_inputs={},
            shots=1,
        )


def _domain_program(
    declaration: Program,
    *,
    compiler_inputs: Mapping[str, ValueType] | None = None,
) -> DomainProgramDef:
    """Project a unified declaration into core's domain program seam."""

    from ._parameter_reads import QuantumParameterReads

    repeat_input_ids = {
        input_handle.id
        for input_handle in _summarize_fragment(declaration.body).repeat_inputs
    }
    return _core_domain_program(
        declaration.id,
        dialect_id=QUANTUM_PROGRAM_DIALECT_ID,
        dialect_version=QUANTUM_PROGRAM_DIALECT_VERSION,
        body=declaration,
        parameter_reads=(
            QuantumParameterReads(declaration.body, declaration.recipes)
            if declaration.recipes is not None
            else None
        ),
        inputs={
            port.id: program_port_type(
                port,
                non_negative=port.id in repeat_input_ids,
            )
            for port in declaration.ports
        },
        compiler_inputs=compiler_inputs,
        results={result.id: result for result in declaration.results},
    )


def _program_call(
    program: Program,
    instance_id: str,
    /,
    *,
    inputs: Mapping[str, object],
    compiler_inputs: Mapping[str, ComputeInput],
    shots: ComputeInput,
    key: DomainCallKey | None = None,
) -> QuantumProgramCall:
    """Create one native domain occurrence from a closed program definition."""

    expected = {port.id for port in program.ports}
    supplied = set(inputs)
    missing = sorted(expected - supplied)
    unknown = sorted(supplied - expected)
    if missing or unknown:
        details: list[str] = []
        if missing:
            details.append("missing " + ", ".join(repr(item) for item in missing))
        if unknown:
            details.append("unknown " + ", ".join(repr(item) for item in unknown))
        raise ValueError("invalid quantum program call inputs: " + "; ".join(details))

    normalized_compiler_inputs = {
        name: _normalize_compiler_input(name, value)
        for name, value in compiler_inputs.items()
    }
    domain = _domain_program(
        program,
        compiler_inputs={
            name: value.value_type for name, value in normalized_compiler_inputs.items()
        },
    )
    normalized_shots = _normalize_shots(shots)
    normalized_inputs = {
        port.id: _normalize_program_input(port, inputs[port.id])
        for port in program.ports
    }
    call = create_domain_call_internal(
        domain,
        id=instance_id,
        inputs=normalized_inputs,
        compiler_inputs=normalized_compiler_inputs,
        result_products={
            result.id: ModuleProductDecl(
                id=result.id,
                value_spec=ProductValueSpec(
                    unit=result.contract.unit,
                    dtype=result.contract.dtype,
                    axes=(
                        *(
                            (
                                entity_axis(
                                    "entity",
                                    cast(
                                        "ValueRef",
                                        normalized_inputs[result.entity_set.id],
                                    ),
                                    shared_as=result.entity_set.id,
                                ),
                            )
                            if result.entity_set is not None
                            else ()
                        ),
                        shot_axis(
                            cast("ValueRef | Quantity | float", normalized_shots),
                            shared_as="shot",
                        ),
                        *(
                            product_axis(
                                dimension.id,
                                size=_result_dimension_axis_size(
                                    dimension,
                                    normalized_inputs,
                                ),
                                kind=dimension.kind,
                                unit=dimension.unit,
                                shared_as=dimension.id,
                            )
                            for dimension in result.contract.dimensions
                        ),
                    ),
                ),
                metadata={
                    "quantum.acquisition_kind": result.acquisition_kind.value,
                    "quantum.local_dimensions": tuple(
                        {
                            "id": dimension.id,
                            "kind": dimension.kind,
                            "unit": dimension.unit,
                            "maximum_size": dimension.maximum_size,
                            "size_input_id": dimension.size_input_id,
                        }
                        for dimension in result.contract.dimensions
                    ),
                },
            )
            for result in program.results
        },
        key=key,
    )
    return QuantumProgramCall(
        program=program,
        domain_call=call,
        arguments=tuple(inputs.items()),
        compiler_arguments=tuple(normalized_compiler_inputs.items()),
        shots=shots,
    )


def _result_dimension_axis_size(
    dimension: QuantumResultDimension,
    inputs: Mapping[str, ComputeInput],
) -> int | None:
    """Project fixed call extents and retain scanned extents as ragged axes."""

    input_id = dimension.size_input_id
    if input_id is None:
        return cast("int", dimension.size)
    selected = inputs[input_id]
    if isinstance(selected, int) and not isinstance(selected, bool):
        return selected
    return None


def _normalize_compiler_input(name: str, value: ComputeInput) -> ValueRef:
    if isinstance(value, ValueRef):
        return value
    value_type = literal_scalar_type(value)
    return internal_literal_value_ref(
        value,
        value_type,
        path=("compiler_inputs", name),
    )


def _normalize_program_input(
    port: ProgramPort,
    value: object,
) -> ComputeInput:
    value_type = program_port_type(port)
    if isinstance(value, ValueRef):
        require_assignable(
            value.value_type,
            value_type,
            path=("inputs", port.id),
        )
        return value
    if isinstance(port, QubitSet):
        if isinstance(value, QubitSelectionIntent):
            return qubit_selection_value_ref(value, port.value_type)
        rows = _qubit_set_rows(value, port=port)
        return internal_literal_value_ref(
            rows,
            value_type,
            path=("inputs", port.id),
        )
    if isinstance(port, CouplerSet):
        if isinstance(value, CouplerSelectionIntent):
            return coupler_selection_value_ref(value, port.value_type)
        rows = _entity_set_rows(value, port=port, column="coupler")
        return internal_literal_value_ref(
            rows,
            port.value_type,
            path=("inputs", port.id),
        )
    if isinstance(port, QubitPairSet):
        if isinstance(value, QubitPairSelectionIntent):
            return qubit_pair_selection_value_ref(value, port.value_type)
        rows = _qubit_pair_set_rows(value, port=port)
        return internal_literal_value_ref(
            rows,
            port.value_type,
            path=("inputs", port.id),
        )
    normalized = coerce_literal(
        value_type,
        value,
        path=("inputs", port.id),
    )
    return cast("ComputeInput", normalized)


def _qubit_set_rows(value: object, *, port: QubitSet) -> tuple[dict[str, object], ...]:
    return _entity_set_rows(value, port=port, column="qubit")


def _entity_set_rows(
    value: object,
    *,
    port: QubitSet | CouplerSet,
    column: str,
) -> tuple[dict[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TypeError(f"inputs.{port.id}: expected a sequence of logical entities")
    selected: list[dict[str, object]] = []
    for item in value:
        if isinstance(item, Mapping):
            selected.append(dict(cast("Mapping[str, object]", item)))
        else:
            selected.append({column: item})
    if not selected:
        raise ValueError(f"inputs.{port.id}: entity set must not be empty")
    return tuple(selected)


def _qubit_pair_set_rows(
    value: object,
    *,
    port: QubitPairSet,
) -> tuple[dict[str, object], ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise TypeError(f"inputs.{port.id}: expected a sequence of qubit-pair rows")
    rows = tuple(dict(cast("Mapping[str, object]", item)) for item in value)
    if not rows:
        raise ValueError(f"inputs.{port.id}: qubit-pair set must not be empty")
    return rows


def _normalize_shots(shots: ComputeInput) -> ComputeInput:
    value_type = ScalarType(IntType(minimum=1))
    if isinstance(shots, ValueRef):
        require_assignable(shots.value_type, value_type, path=("shots",))
        return shots
    return cast(
        "ComputeInput",
        coerce_literal(value_type, shots, path=("shots",)),
    )
