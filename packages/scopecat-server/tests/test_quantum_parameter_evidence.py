from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from scopecat import (
    EntityRef,
    Magnitude,
    Param,
    ParameterModel,
    Quantity,
    param,
    parameter_snapshot,
    parameter_table,
    quantity,
)
from scopecat.config.parameter_reads import compare_parameter_reads
from scopecat.daemon.wire import RunDomainJobTransitionItem
from scopecat.records.execution import (
    DomainInvocationIntent,
    DomainJobInvocationTransition,
)
from scopecat_quantum import authoring as q
from scopecat_quantum._ids import TargetCompileEntryId
from scopecat_quantum.compilation import RecipeTargetCompiler
from scopecat_quantum.pulse_recipes import PulseRecipeProfile
from scopecat_quantum.recipe_bindings import bind_gate_pulse_recipe
from scopecat_quantum.recipe_evidence_records import (
    parameter_evidence_intent,
    read_parameter_evidence,
)
from scopecat_quantum.recipe_queries import recipe_operand, recipe_parameter_inputs
from scopecat_quantum.standard_gates import X90
from scopecat_testkit.domain import domain_execution_identity

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.execution import SQLiteDomainJobTransitions
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


class Calibration(ParameterModel, table="calibration"):
    target: Param[EntityRef] = param(key=True, entity_kind="logical_qubit")
    width: Magnitude[float] = quantity(unit="ns")


def pulse(target: q.Qubit, *, width: Quantity) -> q.QuantumFragment:
    return q.play(
        q.drive(target), q.constant(duration=width, amplitude=Quantity(0.1, "arb"))
    )


@q.program
def experiment(target: q.Qubit) -> q.QuantumFragment:
    return X90(target)


def compiled(snapshot_id: str, width: float):
    target = EntityRef(id="q0", kind="logical_qubit")
    snapshot = parameter_snapshot(
        snapshot_id, tables={Calibration: (Calibration(target=target, width=width),)}
    )
    profile = PulseRecipeProfile(
        bind_gate_pulse_recipe(
            of=X90,
            build=pulse,
            inputs=recipe_parameter_inputs(
                parameter_table(Calibration)
                .lookup(target=recipe_operand())
                .select("width")
            ),
        )
    )
    return RecipeTargetCompiler(profile, snapshot).compile(
        experiment, {"target": target}, entry_id=TargetCompileEntryId(snapshot_id)
    )


def test_compiled_evidence_survives_execution_ledger_restart(tmp_path: Path) -> None:
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    SQLiteProjectStore(sqlite, tmp_path / "objects").bootstrap()
    runs = SQLiteRunRepository(sqlite, tmp_path / "objects")
    run_id = "evidence-run"
    ledger = SQLiteDomainJobTransitions(runs, run_id=run_id)
    target_intent = parameter_evidence_intent(
        ((7, compiled("baseline", 24)), (9, compiled("point-override", 32))),
        target_intent={"realization": "iq"},
    )
    intent, identity = domain_execution_identity(
        run_id=run_id,
        logical_compute_node_id="quantum",
        target_intent=target_intent,
    )
    item = RunDomainJobTransitionItem(
        logical_compute_node_id="quantum",
        point_ordinals=(7, 9),
        transition=DomainJobInvocationTransition(execution_id=identity, intent=intent),
    )
    with sqlite.write_transaction() as connection:
        connection.execute(
            "INSERT INTO scheduler_runs"
            "(submission_id, run_id, state, updated_at, admission_json) "
            "VALUES (?, ?, 'leased', ?, '{}')",
            ("submission", run_id, datetime.now(UTC).isoformat()),
        )
        ledger.commit_in_transaction(connection, item)
    restarted = SQLiteDomainJobTransitions(
        SQLiteRunRepository(
            SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
        ),
        run_id=run_id,
    )
    [saved] = restarted.read(limit=10).items
    assert saved.point_ordinals == (7, 9)
    assert isinstance(saved.transition, DomainJobInvocationTransition)
    assert saved.transition.intent.target_intent["realization"] == "iq"
    record = read_parameter_evidence(saved.transition.intent)
    assert record.coverage == "recipe_keyed_query_values"
    for entry, ordinal, snapshot_id, width in zip(
        record.entries, (7, 9), ("baseline", "point-override"), (24, 32), strict=True
    ):
        assert entry.point_ordinal == ordinal
        assert entry.entry_id == snapshot_id
        resolution = entry.recipes[0].resolution
        assert resolution.snapshot_id == snapshot_id
        assert resolution.inputs == {"width": Quantity(width, "ns")}
        assert resolution.sources["width"][0].table == "calibration"
        current = parameter_snapshot(
            "current",
            tables={
                Calibration: (
                    Calibration(
                        target=EntityRef(id="q0", kind="logical_qubit"), width=width + 1
                    ),
                )
            },
        )
        [difference] = compare_parameter_reads(resolution.parameter_reads, current)
        assert difference.reason == "cells_changed"
        assert difference.columns == ("width",)
    # Recording compilation intent does not assert successful physical execution.
    assert restarted.read_current(limit=10).items[0].state == "invocation_unknown"
    changed = saved.transition.intent.model_dump()
    changed["target_intent"] = parameter_evidence_intent(
        ((7, compiled("baseline", 25)),),
        target_intent={"realization": "iq"},
    )
    with pytest.raises(ValidationError, match="fingerprint"):
        DomainInvocationIntent.model_validate(changed)


def test_evidence_writer_rejects_ambiguous_attachment() -> None:
    entry = compiled("point", 24)
    with pytest.raises(ValidationError, match="repeats a point/entry"):
        parameter_evidence_intent(((0, entry), (0, entry)), target_intent={})
    attached = parameter_evidence_intent(((0, entry),), target_intent={})
    with pytest.raises(ValueError, match="already contains"):
        parameter_evidence_intent(((0, entry),), target_intent=attached)
