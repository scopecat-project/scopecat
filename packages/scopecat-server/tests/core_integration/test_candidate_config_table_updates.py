from __future__ import annotations

from pathlib import Path

import scopecat as sc
from scopecat.config.documents import load_config_snapshot_document
from scopecat.config.parameter_resolution import resolve_config_parameters
from scopecat.config.registry import (
    CandidateConfigRegistrySource,
)
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Float, Scalar, String, Table, TableColumn
from scopecat.kernel.value_types import Quantity as QuantityType
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.parameter import (
    ParameterDefinition,
    TableParameterValue,
)
from scopecat_testkit.config_registry import (
    activate_candidate_config,
    load_config_registry_config,
)
from scopecat_testkit.instrument_host import compose_test_instruments
from scopecat_testkit.paths import CORE_FIXTURE_DIR as EXAMPLE_DIR
from scopecat_testkit.server.in_process_lab import in_process_lab
from scopecat_testkit.server.runtime import (
    sqlite_config_registry_unit_of_work,
    sqlite_project_services,
)
from scopecat_testkit.signal_instruments import TestSignalInstrumentProvider
from scopecat_testkit.workflow_fixtures import load_invocation


def test_candidate_config_activation_materializes_table_row_updates(
    tmp_path: Path,
) -> None:
    config = _config_with_drive_channels()
    composition = compose_test_instruments(
        config=config,
        provider=TestSignalInstrumentProvider(),
    )
    lab = in_process_lab(
        tmp_path,
        config=config,
        system=composition.system,
        instrument_backend=composition.backend,
    )
    run = lab.prepare(load_invocation()).run()
    analysis = (
        run.analysis("table update fixture")
        .result()
        .propose(
            "drive-channel-update",
            sc.update_parameter_rows(
                "drive_channels",
                key={"channel_id": "xy0"},
                values={"gain": 0.75},
            ),
            sc.insert_parameter_rows(
                "drive_channels",
                rows=[
                    {
                        "channel_id": "xy1",
                        "resource_id": "drive-b",
                        "gain": 0.25,
                        "fixed_if": Quantity(value=120, unit="MHz"),
                    }
                ],
            ),
            sc.delete_parameter_rows(
                "drive_channels",
                key={"channel_id": "remove-me"},
            ),
            reason="table row updates",
        )
    )
    outcome = analysis.save()
    candidate = outcome.candidate_config()
    proposal = outcome.parameter_proposals[0]

    assert len(proposal.deltas) == 1
    assert proposal.deltas[0].parameter_id == "drive_channels"
    assert proposal.deltas[0].cells is not None
    existing = [
        cell for cell in proposal.deltas[0].cells if cell.key == {"channel_id": "xy0"}
    ]
    assert len(existing) == 1
    assert existing[0].field == "gain"
    assert existing[0].before == 0.5
    assert existing[0].after == 0.75
    lab.review_parameter_proposal(run, proposal.id)

    activation = activate_candidate_config(
        candidate=candidate,
        services=sqlite_project_services(tmp_path),
        actor="operator",
        entry_id="candidate-table-update",
    )
    entry = activation.entry
    assert isinstance(entry.source, CandidateConfigRegistrySource)
    assert entry.source.proposal_id == "drive-channel-update"

    candidate_config = load_config_registry_config(
        entry_id=entry.id,
        unit_of_work=sqlite_config_registry_unit_of_work(tmp_path),
    )
    table = candidate_config.parameter_snapshot.get("drive_channels")
    assert isinstance(table, TableParameterValue)
    assert table.rows == (
        {
            "channel_id": "xy0",
            "resource_id": "drive-a",
            "gain": 0.75,
            "fixed_if": Quantity(value=0.1, unit="GHz"),
        },
        {
            "channel_id": "xy1",
            "resource_id": "drive-b",
            "gain": 0.25,
            "fixed_if": Quantity(value=120, unit="MHz"),
        },
    )

    resolved = resolve_config_parameters(candidate_config)
    assert not resolved.problems
    assert resolved.data.table_rows("drive_channels")[0]["fixed_if"] == Quantity(
        100, "MHz"
    )


def _config_with_drive_channels() -> ConfigProfileSnapshot:
    config = load_config_snapshot_document(EXAMPLE_DIR / "config-snapshot.json")
    definition = ParameterDefinition(
        id="drive_channels",
        value_type=Table(
            columns=(
                TableColumn(id="channel_id", value_type=Scalar(String())),
                TableColumn(id="resource_id", value_type=Scalar(String())),
                TableColumn(id="gain", value_type=Scalar(Float())),
                TableColumn(
                    id="fixed_if",
                    value_type=Scalar(QuantityType(unit="MHz")),
                ),
            ),
            primary_key=("channel_id",),
        ),
    )
    catalog = config.parameter_catalog.model_copy(
        update={"definitions": (*config.parameter_catalog.definitions, definition)}
    )
    system = config.system.model_copy(update={"parameter_catalog": catalog})
    table = TableParameterValue(
        id="drive_channels",
        rows=(
            {
                "channel_id": "xy0",
                "resource_id": "drive-a",
                "gain": 0.5,
                "fixed_if": Quantity(value=0.1, unit="GHz"),
            },
            {
                "channel_id": "remove-me",
                "resource_id": "drive-stale",
                "gain": 0.1,
                "fixed_if": Quantity(value=80, unit="MHz"),
            },
        ),
    )
    snapshot = config.parameter_snapshot.model_copy(
        update={"values": (*config.parameter_snapshot.values, table)}
    )
    return config.model_copy(update={"system": system, "parameter_snapshot": snapshot})
