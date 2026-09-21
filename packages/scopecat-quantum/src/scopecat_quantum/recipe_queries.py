"""Quantum call context for public parameter projections."""

from dataclasses import dataclass, replace

from scopecat.authoring.parameter_queries import (
    ParameterInputs,
    ParameterProjection,
    ParameterQueryResult,
    QueryInput,
)
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.frozen import FrozenMapping
from scopecat.records.parameter import ParameterSnapshot

from scopecat_quantum.circuits import Measure
from scopecat_quantum.gates import GateCall
from scopecat_quantum.recipe_evidence import ResolvedRecipeInputs


def recipe_operand(index: int = 0) -> QueryInput:
    """Select an operation's logical qubit as a typed entity key."""
    if index < 0:
        raise ValueError("operand index must be nonnegative")
    return QueryInput(f"operand:{index}")


def recipe_operation() -> QueryInput:
    """Select the logical gate ID (not available for a measurement)."""
    return QueryInput("operation")


@dataclass(frozen=True, slots=True)
class RecipeParameterInputs:
    projection: ParameterProjection | ParameterInputs

    def __call__(
        self, snapshot: ParameterSnapshot, call: GateCall | Measure
    ) -> ResolvedRecipeInputs:
        qubits = call.qubits if isinstance(call, GateCall) else (call.qubit,)
        context: dict[str, object] = {
            f"operand:{index}": EntityRef(id=qubit.value, kind="logical_qubit")
            for index, qubit in enumerate(qubits)
        }
        if isinstance(call, GateCall):
            context["operation"] = call.gate_id.value
        result = self.projection.resolve(snapshot, context)
        sources = (
            FrozenMapping(
                (
                    name,
                    (
                        replace(
                            result,
                            fields=FrozenMapping(((name, result.fields[name]),)),
                            values=FrozenMapping(((name, value),)),
                        ),
                    ),
                )
                for name, value in result.values.items()
            )
            if isinstance(result, ParameterQueryResult)
            else result.sources
        )
        return ResolvedRecipeInputs(snapshot.id, result.values, sources)


def recipe_parameter_inputs(
    projection: ParameterProjection | ParameterInputs,
) -> RecipeParameterInputs:
    """Adapt a declarative projection to a recipe's named input boundary."""
    return RecipeParameterInputs(projection)
