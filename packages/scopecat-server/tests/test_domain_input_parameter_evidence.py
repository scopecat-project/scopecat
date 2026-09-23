from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
from scopecat.compiler.bound_specialization import specialize_bound_facts
from scopecat.compiler.parameter_overlays import PointParameterOverlay
from scopecat.compiler.point_domain import PointDomain
from scopecat.config.environment import build_config_environment
from scopecat.daemon.wire import RunDomainJobTransitionItem
from scopecat.domain.program import DomainInputPort, DomainProgramDef
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.kernel.value_types import Scalar
from scopecat.planning.domain_bridge import (
    make_domain_batch_request,
    make_domain_call_view,
)
from scopecat.planning.point_materialization import prepare_bound_points
from scopecat.program.expressions import parameter_lookup
from scopecat.program.point_domain import point_axis_values
from scopecat.records.execution import DomainJobInvocationTransition
from scopecat.sdk.domain.parameter_evidence import (
    attach_domain_input_reads,
    read_domain_input_reads,
)
from scopecat_testkit.bound_program import (
    DomainExecutionFixture,
    bind_program_facts,
    program_fixture,
)
from scopecat_testkit.domain import domain_execution_identity
from scopecat_testkit.materialized_effects import config_with_physical_resources
from scopecat_testkit.parameter_fixtures import READOUT_FREQUENCY_LOOKUP, parameters

from scopecat_server.storage.sqlite.connection import SQLiteDatabase
from scopecat_server.storage.sqlite.execution import SQLiteDomainJobTransitions
from scopecat_server.storage.sqlite.project_store import SQLiteProjectStore
from scopecat_server.storage.sqlite.run_repository import SQLiteRunRepository


@pytest.mark.parametrize("fold", [False, True])
def test_point_input_reads_survive_ledger_reopen(tmp_path: Path, fold: bool) -> None:
    frequency = Scalar(QuantityType(dimension="frequency"))
    execution = DomainExecutionFixture(
        id="compile",
        program=DomainProgramDef(
            id="input-reader",
            dialect_id="test",
            dialect_version="1",
            body=object(),
            input_ports=(
                DomainInputPort("frequency", frequency),
                DomainInputPort("reference", frequency),
            ),
        ),
        inputs={
            "frequency": parameter_lookup(
                READOUT_FREQUENCY_LOOKUP, key={"device_id": "r0"}
            ),
            "reference": parameter_lookup(
                READOUT_FREQUENCY_LOOKUP, key={"device_id": "r1"}
            ),
        },
    )
    spec = program_fixture(
        point_domain=PointDomain(
            axes=(
                point_axis_values(
                    "sweep", frequency, (Quantity(5.9, "GHz"), Quantity(6.2, "GHz"))
                ),
            )
        ),
        parameter_overlays=[
            PointParameterOverlay(
                "readout_devices",
                0,
                {"device_id": "r0"},
                "frequency",
                "sweep",
                frequency,
            )
        ],
        domain_execution=execution,
    )
    environment = replace(
        build_config_environment(config_with_physical_resources({})),
        parameters=parameters(),
    )
    if fold:
        spec = replace(
            spec,
            bindings=specialize_bound_facts(
                spec.logical, spec.bindings, parameters=environment.parameters
            ),
        )
    bound = bind_program_facts(spec, environment)
    context = make_domain_batch_request(
        make_domain_call_view(bound, execution.id, ()),
        prepare_bound_points(bound),
        (1, 0),
        legal_cut_offsets=(1, 2),
        batch_ordinal=7,
    )
    intent, identity = domain_execution_identity(
        run_id="reads",
        logical_compute_node_id="domain",
        target_intent=attach_domain_input_reads(context, {"profile": "lab"}),
    )
    sqlite = SQLiteDatabase(tmp_path / "control.sqlite3")
    SQLiteProjectStore(sqlite, tmp_path / "objects").bootstrap()
    ledger = SQLiteDomainJobTransitions(
        SQLiteRunRepository(sqlite, tmp_path / "objects"), run_id="reads"
    )
    item = RunDomainJobTransitionItem(
        logical_compute_node_id="domain",
        point_ordinals=(1, 0),
        transition=DomainJobInvocationTransition(execution_id=identity, intent=intent),
    )
    with sqlite.write_transaction() as connection:
        connection.execute(
            "INSERT INTO scheduler_runs"
            "(submission_id, run_id, state, updated_at, admission_json) "
            "VALUES (?, ?, 'leased', ?, '{}')",
            ("submission", "reads", datetime.now(UTC).isoformat()),
        )
        ledger.commit_in_transaction(connection, item)
    reopened = SQLiteDomainJobTransitions(
        SQLiteRunRepository(
            SQLiteDatabase(tmp_path / "control.sqlite3"), tmp_path / "objects"
        ),
        run_id="reads",
    )
    [saved] = reopened.read(limit=10).items
    assert isinstance(saved.transition, DomainJobInvocationTransition)
    evidence = read_domain_input_reads(saved.transition.intent)
    assert evidence.entries == context.inputs.parameter_reads
    assert [entry.point_ordinal for entry in evidence.entries] == [1, 1, 0, 0]
    if fold:
        assert all(entry.evidence.keyed == () for entry in evidence.entries)
        [binding] = evidence.binding
        assert binding.parameter_scope == "base_configuration"
        [read] = binding.evidence.keyed
        assert read.key[0].value == "r1"
        assert (
            read.cells[0].value
            == environment.parameters.lookup_row(
                "readout_devices", {"device_id": "r1"}
            )["frequency"]
        )
    else:
        assert [
            entry.evidence.keyed[0].cells[0].value
            for entry in evidence.entries
            if entry.input_id == "frequency"
        ] == [Quantity(6.2, "GHz"), Quantity(5.9, "GHz")]
    assert "frontend_binding_not_captured" in evidence.incomplete_reasons
    assert ("specialization_binding_not_captured" in evidence.incomplete_reasons) == (
        not fold
    )
    assert saved.transition.intent.target_intent["profile"] == "lab"
    assert reopened.read_current(limit=10).items[0].state == "invocation_unknown"
    subset = make_domain_batch_request(
        make_domain_call_view(bound, execution.id, ()),
        prepare_bound_points(bound),
        (1,),
        legal_cut_offsets=(1,),
        batch_ordinal=8,
    )
    assert subset.inputs.parameter_reads == evidence.entries[:2]
    assert subset.inputs.binding_parameter_reads == evidence.binding
    assert context.inputs.parameter_reads == evidence.entries
