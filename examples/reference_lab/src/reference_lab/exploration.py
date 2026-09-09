"""Maintainer-owned isolated exploration fixtures, using existing public records.

Context IDs label provenance only. Each case supplies its configuration explicitly;
this module is not a parameter-context resolver or a device simulation framework.
"""

from __future__ import annotations

from dataclasses import dataclass

import scopecat as sc
from scopecat.api.lab import LabClient
from scopecat.kernel.entity import EntityRef
from scopecat.records.config import ConfigProfileSnapshot, Topology
from scopecat.records.parameter import ParameterSnapshot, TableParameterValue
from scopecat.records.sample import SampleRevisionDraft

from reference_lab.configuration import bootstrap_config
from reference_lab.parameters import DRIVE_CARRIER_FREQUENCY, QUBIT, QUBITS
from reference_lab.workflows.exploratory_signal import exploratory_signal


@dataclass(frozen=True, slots=True)
class ExplorationCase:
    sample_id: str
    context_id: str
    config: ConfigProfileSnapshot


def exploration_config(carrier: sc.Quantity | None) -> ConfigProfileSnapshot:
    """An explicit trial snapshot; None deliberately leaves q0's carrier missing."""
    base = bootstrap_config()
    table = base.parameter_snapshot.get(QUBITS.id)
    assert isinstance(table, TableParameterValue)
    rows = [dict(row) for row in table.rows]
    row = next(
        row for row in rows if row[QUBIT.id] == EntityRef(id="q0", kind="logical_qubit")
    )
    if carrier is None:
        del row[DRIVE_CARRIER_FREQUENCY.id]
    else:
        row[DRIVE_CARRIER_FREQUENCY.id] = carrier
    parameters = ParameterSnapshot(
        id=base.parameter_snapshot.id,
        values=tuple(
            TableParameterValue(id=QUBITS.id, rows=tuple(rows))
            if value.id == QUBITS.id
            else value
            for value in base.parameter_snapshot.values
        ),
    )
    return base.model_copy(update={"parameter_snapshot": parameters})


def exploration_cases() -> tuple[ExplorationCase, ...]:
    return tuple(
        ExplorationCase(
            sample_id=f"exploration-{sample}",
            context_id=point,
            config=exploration_config(sc.Quantity(carrier, "GHz")),
        )
        for sample, point, carrier in (
            ("a", "parked", 4.8),
            ("a", "shifted", 4.9),
            ("b", "parked", 5.0),
            ("b", "shifted", 5.1),
        )
    )


def seed_exploration(lab: LabClient) -> tuple[str, ...]:
    """Retain four real Scopecat runs in a caller-owned, fresh reference project."""
    for sample in ("a", "b"):
        lab.samples.create(
            f"exploration-{sample}",
            kind="synthetic",
            content=SampleRevisionDraft(
                display_name=f"Synthetic exploration {sample.upper()}",
                topology=Topology(entities=[EntityRef(id="q0", kind="logical_qubit")]),
            ),
            note="Software exploration fixture; no physical sample",
        )
    return tuple(
        lab.run(
            exploratory_signal(),
            config=case.config,
            sample=lab.samples.handle(case.sample_id).selector(
                context_id=case.context_id
            ),
            name=f"{case.sample_id} / {case.context_id}",
        ).id
        for case in exploration_cases()
    )
