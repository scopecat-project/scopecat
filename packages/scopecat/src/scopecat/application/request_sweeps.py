"""Compose retained outer axes using existing point-domain and overlay semantics."""

from dataclasses import replace
from typing import Literal

from scopecat.authoring.scans import axis
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.domain.program import DomainParameterRead
from scopecat.program.definitions import ExperimentInvocation
from scopecat.program.expression_analysis import scalar_nodes
from scopecat.program.expressions import LiteralScalarExpr, ParameterLookupScalarExpr
from scopecat.program.scans import (
    AxisSpec,
    PointsSpec,
    ValuesScanSource,
    parameter_overlay_cell,
)
from scopecat.program.values import coordinate, parameter_lookup
from scopecat.records.parameter import ParameterSnapshot
from scopecat.records.request_sweep import ParameterSweep


def compose_request_sweeps[T](
    invocation: ExperimentInvocation[T],
    *,
    mode: Literal["cartesian", "paired"],
    parameters: tuple[ParameterSweep, ...],
    snapshot: ParameterSnapshot | None = None,
) -> ExperimentInvocation[T]:
    edited = invocation
    if parameters:
        logical = compile_invocation(invocation).program
        lookups = tuple(
            node
            for value in logical.scalar_values.values()
            for node in scalar_nodes(value)
            if isinstance(node, ParameterLookupScalarExpr)
        )
        domain_reads = tuple(
            read
            for execution in logical.program.domain_executions
            if snapshot is not None and execution.program.parameter_reads is not None
            for read in execution.program.parameter_reads(
                {
                    name: value.value
                    for name, value_id in execution.inputs
                    for value in (logical.scalar_values.get(value_id),)
                    if isinstance(value, LiteralScalarExpr)
                },
                snapshot,
            )
        )
        names = {item.id for item in invocation.point_plan.domain.axes}
        for sweep in parameters:
            if sweep.name in names:
                raise ValueError(f"sweep coordinate {sweep.name!r} already exists")
            names.add(sweep.name)
            consumed = any(
                node.use.table_id == sweep.table
                and node.use.column_id == sweep.column
                and len(node.key) == len(sweep.key)
                and all(
                    isinstance(value, LiteralScalarExpr)
                    and value.value == sweep.key.get(key)
                    for key, value in node.key.items()
                )
                for node in lookups
            )
            consumed = consumed or any(
                _reads_cell(item, sweep)
                and not any(
                    _reads_cell(selection, edited_cell)
                    for selection in item.selection_reads
                    for edited_cell in parameters
                )
                and not any(
                    _reads_axis(selection, axis)
                    for selection in item.selection_reads
                    for axis in logical.program.parameter_overlays
                )
                for item in domain_reads
            )
            if not consumed:
                raise ValueError(
                    f"{sweep.name}: experiment does not consume "
                    "this exact parameter cell"
                )
            target = parameter_lookup(
                sweep.table,
                key=sweep.key,
                column=sweep.column,
                value_type=sweep.value_type,
            )
            edited = edited.with_axis(
                axis(
                    coordinate(sweep.name, sweep.value_type),
                    sweep.values,
                    overlay=target,
                )
            )
    if mode == "cartesian":
        return edited
    axes = edited.point_plan.domain.axes
    if any(not isinstance(item.source, ValuesScanSource) for item in axes):
        raise ValueError("paired request scans require explicit values")
    sources = tuple(
        item.source for item in axes if isinstance(item.source, ValuesScanSource)
    )
    lengths = {
        len(source.values)
        for item, source in zip(axes, sources, strict=True)
        if item.mode == "scan"
    }
    if len(lengths) != 1:
        raise ValueError("paired scans require equal nonzero lengths")
    size = next(iter(lengths))
    if size == 0:
        raise ValueError("paired scans require equal nonzero lengths")
    paired = tuple(
        replace(item, source=ValuesScanSource(source.values * size))
        if item.mode == "fixed" and len(source.values) == 1
        else item
        for item, source in zip(axes, sources, strict=True)
    )
    return replace(
        edited,
        point_plan_override=replace(edited.point_plan, domain=PointsSpec(paired)),
    )


def _reads_cell(read: DomainParameterRead, sweep: ParameterSweep) -> bool:
    return (
        read.table == sweep.table
        and dict(read.key) == dict(sweep.key)
        and sweep.column in read.columns
    )


def _reads_axis(read: DomainParameterRead, axis: AxisSpec) -> bool:
    lookup, key = parameter_overlay_cell(axis)
    return (
        read.table == lookup.table_id
        and dict(read.key) == dict(key)
        and lookup.column_id in read.columns
    )
