"""Notebook-side compiler and experiment-system composition."""

from __future__ import annotations

from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.planning.system import ExperimentSystem
from scopecat.records.config import ConfigProfileSnapshot

from reference_lab.compiler import QuantumLabCompiler
from reference_lab.payloads import reference_lab_payload_codecs
from reference_lab.targets.list_mode import (
    ListModePlacementProvider,
    configured_list_mode_target,
)
from reference_lab.virtual_lab.execution import virtual_quantum_job_runtime


def reference_lab_system(
    *,
    config: ConfigProfileSnapshot,
    instrument_catalog: InstrumentContractCatalog,
    placement_provider: ListModePlacementProvider | None = None,
) -> ExperimentSystem:
    """Compose one process-local system for notebook execution.

    Keeping domain dispatch at this single boundary gives every example the
    same routing, resource model, and target pipeline while each operation
    still specializes its own accepted parameter snapshot.
    """

    return ExperimentSystem(
        instrument_catalog=instrument_catalog,
        domain_compiler=QuantumLabCompiler(
            target=configured_list_mode_target(config, instrument_catalog),
            job_runtime_selector=virtual_quantum_job_runtime,
            placement_provider=placement_provider,
        ),
        payload_codecs=reference_lab_payload_codecs(),
    )


__all__ = [
    "reference_lab_system",
]
