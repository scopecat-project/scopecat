"""Domain-program definitions, calls, and ordered execution.

Domain bodies and result contracts are opaque transient values.  Core owns
only their stable identities, typed value ports, and logical product bindings;
an adapter for the selected dialect owns interpretation of the body.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from scopecat.domain.program import (
    DomainInputPort,
    DomainParameterReads,
    DomainProgramDef,
    DomainResultPort,
)
from scopecat.kernel.payloads import PayloadValue
from scopecat.program.identities import DomainCallKey
from scopecat.program.input_capture import capture_runtime_input
from scopecat.program.operations import ComputeNodeInputValue
from scopecat.program.products import (
    ModuleProductDecl,
    ProductRef,
    ProductRefs,
    prefix_product_decl,
)
from scopecat.program.value_refs import ValueRef
from scopecat.program.value_types import ValueType


@dataclass(frozen=True, slots=True)
class DomainExecution:
    """One identified domain-program effect placed in an ordered program."""

    id: str
    program: DomainProgramDef
    input_bindings: tuple[tuple[str, ComputeNodeInputValue], ...] = ()
    compiler_input_bindings: tuple[tuple[str, ComputeNodeInputValue], ...] = ()
    result_bindings: tuple[tuple[str, ProductRef], ...] = ()


@dataclass(frozen=True, slots=True, repr=False)
class DomainCall:
    """One native domain-program occurrence with owned result products."""

    id: str
    key: DomainCallKey
    execution: DomainExecution
    product_declarations: tuple[ModuleProductDecl, ...]
    results: ProductRefs


def domain_program(
    id: str,
    *,
    dialect_id: str,
    dialect_version: str,
    body: object,
    inputs: Mapping[str, ValueType] | None = None,
    compiler_inputs: Mapping[str, ValueType] | None = None,
    results: Mapping[str, object | None] | None = None,
    parameter_reads: DomainParameterReads | None = None,
) -> DomainProgramDef:
    """Declare an opaque program with ordered typed input and result ports."""

    return DomainProgramDef(
        id=id,
        dialect_id=dialect_id,
        dialect_version=dialect_version,
        body=body,
        parameter_reads=parameter_reads,
        input_ports=tuple(
            DomainInputPort(port_id, value_type)
            for port_id, value_type in (inputs or {}).items()
        ),
        compiler_input_ports=tuple(
            DomainInputPort(port_id, value_type)
            for port_id, value_type in (compiler_inputs or {}).items()
        ),
        result_ports=tuple(
            DomainResultPort(port_id, contract)
            for port_id, contract in (results or {}).items()
        ),
    )


def domain_execution(
    program: DomainProgramDef,
    *,
    id: str | None = None,
    inputs: Mapping[str, ComputeNodeInputValue] | None = None,
    compiler_inputs: Mapping[str, ComputeNodeInputValue] | None = None,
    results: Mapping[str, ProductRef] | None = None,
) -> DomainExecution:
    """Bind one module call to typed values and composed products."""

    execution_id = program.id if id is None else id
    if not execution_id:
        raise ValueError("domain execution id must be non-empty")
    selected_inputs = inputs or {}
    selected_compiler_inputs = compiler_inputs or {}
    selected_results = results or {}
    _require_exact_keys(
        "domain execution inputs",
        selected_inputs,
        tuple(port.id for port in program.input_ports),
    )
    _require_exact_keys(
        "domain execution compiler inputs",
        selected_compiler_inputs,
        tuple(port.id for port in program.compiler_input_ports),
    )
    _require_exact_keys(
        "domain execution results",
        selected_results,
        tuple(port.id for port in program.result_ports),
    )
    return DomainExecution(
        id=execution_id,
        program=program,
        input_bindings=tuple(
            (port.id, _capture_domain_input(selected_inputs[port.id]))
            for port in program.input_ports
        ),
        compiler_input_bindings=tuple(
            (port.id, _capture_domain_input(selected_compiler_inputs[port.id]))
            for port in program.compiler_input_ports
        ),
        result_bindings=tuple(
            (
                port.id,
                selected_results[port.id],
            )
            for port in program.result_ports
        ),
    )


def create_domain_call_internal(
    program: DomainProgramDef,
    *,
    id: str,
    inputs: Mapping[str, ComputeNodeInputValue] | None = None,
    compiler_inputs: Mapping[str, ComputeNodeInputValue] | None = None,
    result_products: Mapping[str, ModuleProductDecl] | None = None,
    key: DomainCallKey | None = None,
) -> DomainCall:
    """Create one occurrence while keeping result ownership internally coherent."""

    if not id:
        raise ValueError("domain call id must be non-empty")
    selected_products = result_products or {}
    _require_exact_keys(
        "domain call result products",
        selected_products,
        tuple(port.id for port in program.result_ports),
    )
    call_key = key or DomainCallKey.fresh()
    declarations: list[ModuleProductDecl] = []
    results: dict[str, ProductRef] = {}
    for port in program.result_ports:
        product = selected_products[port.id]
        if product.id != port.id or product.scope or product.origin:
            raise ValueError(
                "domain call result products must be unscoped declarations "
                "named after their result ports"
            )
        declaration = prefix_product_decl(product, id, origin=(call_key,))
        declarations.append(declaration)
        results[port.id] = ProductRef.from_declaration(declaration)
    result_refs = ProductRefs(results)
    execution = domain_execution(
        program,
        id=f"{id}/{program.id}",
        inputs=inputs,
        compiler_inputs=compiler_inputs,
        results=result_refs,
    )
    return DomainCall(
        id=id,
        key=call_key,
        execution=execution,
        product_declarations=tuple(declarations),
        results=result_refs,
    )


def _require_exact_keys(
    label: str,
    values: Mapping[str, object],
    expected: tuple[str, ...],
) -> None:
    unknown = sorted(set(values) - set(expected))
    missing = sorted(set(expected) - set(values))
    if unknown or missing:
        details: list[str] = []
        if unknown:
            details.append("unknown: " + ", ".join(unknown))
        if missing:
            details.append("missing: " + ", ".join(missing))
        raise ValueError(f"{label} must match declared ports ({'; '.join(details)})")


def _capture_domain_input(value: ComputeNodeInputValue) -> ComputeNodeInputValue:
    if isinstance(value, ValueRef):
        return value
    if isinstance(value, PayloadValue):
        return value
    return cast("ComputeNodeInputValue", capture_runtime_input(value))
