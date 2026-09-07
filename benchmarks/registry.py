"""Canonical registry for the local benchmark suite."""

from __future__ import annotations

from .model import BenchmarkCase

BENCHMARK_CASES = (
    BenchmarkCase(
        id="entity-reads",
        kind="component",
        module="benchmarks.component.entity_reads",
        summary="Selected entity wire, native chunk, working set, and sampled RSS",
    ),
    BenchmarkCase(
        id="scan-execution",
        kind="e2e",
        module="benchmarks.e2e.scan_execution",
        summary="Production scan latency, working set, and durable retention",
    ),
    BenchmarkCase(
        id="scale-suite",
        kind="e2e",
        module="benchmarks.e2e.scale_suite",
        summary="Named 1/4/16/64-qubit acceptance and benchmark profiles",
    ),
    BenchmarkCase(
        id="adaptive-context",
        kind="component",
        module="benchmarks.component.adaptive_context",
        summary="Bounded optimizer decisions and completed-point observations",
    ),
    BenchmarkCase(
        id="list-mode-compiler",
        kind="component",
        module="benchmarks.component.list_mode_compiler",
        summary="Cold/warm target compilation and retained cache budgets",
    ),
    BenchmarkCase(
        id="historical-project",
        kind="component",
        module="benchmarks.component.historical_project",
        summary="Paged daemon reads from 10k-run long-lived projects",
    ),
    BenchmarkCase(
        id="quantum-program",
        kind="component",
        module="benchmarks.component.quantum_program",
        summary="Bounded quantum authoring, lowering, and local result shape",
    ),
    BenchmarkCase(
        id="inspection-index",
        kind="micro",
        module="benchmarks.micro.inspection_index",
        summary="Exact-node and inverted-index inspection projection",
    ),
    BenchmarkCase(
        id="payload-attachments",
        kind="micro",
        module="benchmarks.micro.payload_attachments",
        summary="Multi-array payload encode, flatten, restore, and decode",
    ),
)

_CASES_BY_ID = {case.id: case for case in BENCHMARK_CASES}


def benchmark_case(case_id: str) -> BenchmarkCase:
    """Resolve one registered case by stable CLI identity."""

    try:
        return _CASES_BY_ID[case_id]
    except KeyError:
        choices = ", ".join(_CASES_BY_ID)
        raise ValueError(
            f"unknown benchmark {case_id!r}; choose from {choices}"
        ) from None


__all__ = ["BENCHMARK_CASES", "benchmark_case"]
