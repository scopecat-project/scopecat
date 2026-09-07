"""Symbolic operations shared by assembly and resolution.

The objects in this module describe author intent only.  Turning them into
compiler and planning models is deliberately owned by :mod:`assembly` so the
data model does not depend on its lowering pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass

from scopecat.kernel.entity import EntityRef
from scopecat.kernel.payloads import PayloadValue
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.symbols import SymbolId
from scopecat.program.identities import (
    ComputeDeclarationKey,
    InvocationKey,
)
from scopecat.program.value_refs import (
    ValueRef,
    internal_input_value_ref,
    internal_operation_result_value_ref,
)
from scopecat.program.value_types import DataType, ValueType

type ComputeNodeInputValue = (
    ValueRef
    | Quantity
    | str
    | int
    | float
    | complex
    | bool
    | EntityRef
    | PayloadValue
    | None
)


@dataclass(frozen=True, slots=True)
class ModuleOperationDecl:
    """Callable-free semantic declaration for one module-local operation."""

    id: str
    declaration_key: ComputeDeclarationKey
    output_type: DataType
    input_types: tuple[tuple[str, DataType], ...]
    inputs: tuple[tuple[str, ComputeNodeInputValue], ...] = ()
    scope: tuple[str, ...] = ()
    instance_path: tuple[InvocationKey, ...] = ()

    @property
    def operation_id(self) -> SymbolId:
        return SymbolId(scope=self.scope, local_id=self.id)

    @property
    def result(self) -> ValueRef:
        return internal_operation_result_value_ref(
            self.operation_id,
            self.output_type,
            origin=(*self.instance_path, self.declaration_key),
        )


@dataclass(frozen=True)
class ModuleInputPort:
    id: str
    value_type: ValueType

    @property
    def ref(self) -> ValueRef:
        return internal_input_value_ref(self.id, self.value_type)
