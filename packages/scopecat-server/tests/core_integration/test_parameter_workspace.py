"""Author edits use real durable registry entries, with no daemon or devices."""

from pathlib import Path

import pytest
from scopecat.api.parameters import ParameterTable, ParameterWorkspace, _TableData
from scopecat.config.contexts import apply_context_overrides, context_value_origins
from scopecat.config.parameter_updates import ParameterUpdate
from scopecat.config.registry import (
    ConfigRevision,
    DirectConfigRevisionSource,
    load_active_config_registry_snapshot,
    publish_config_revision,
)
from scopecat.config.registry.records import ContextConfigRegistrySource
from scopecat.config.registry.service import (
    load_config_registry_entry_snapshot,
    save_config_context,
)
from scopecat.daemon.views import ConfigContextResolution, ConfigEntryView
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import Conflict
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import (
    Entity,
    Float,
    Scalar,
    String,
    Table,
    TableColumn,
)
from scopecat.records.config import config_content_hash
from scopecat.records.config_context import ConfigContextRef, ContextRunConfigSource
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    TableParameterValue,
)
from scopecat.records.sample import SampleBinding, SampleSelector
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.runtime import sqlite_config_registry_unit_of_work


class RegistryOperations:
    """Transport substitute; persistence and resolution use production functions."""

    def __init__(self, path: Path) -> None:
        self.uow = sqlite_config_registry_unit_of_work(path)
        self.sample = SampleBinding(
            role="subject",
            sample_id="sample",
            revision=1,
            content_hash="sha256:" + "a" * 64,
            kind="synthetic",
            display_name="sample",
            context_id="parked",
        )

    def entry(self, entry_id: str) -> ConfigEntryView:
        saved = load_config_registry_entry_snapshot(
            entry_id=entry_id, unit_of_work=self.uow
        )
        return ConfigEntryView(entry=saved.entry, config=saved.config)

    def resolve_context(
        self,
        context: ConfigContextRef,
        *,
        overrides: tuple[ParameterUpdate, ...] = (),
    ) -> ConfigContextResolution:
        saved = self.entry(context.entry_id)
        assert saved.entry.content_hash == context.content_hash
        assert isinstance(saved.entry.source, ContextConfigRegistrySource)
        config = apply_context_overrides(saved.config, overrides)
        return ConfigContextResolution(
            config=config,
            config_source=ContextRunConfigSource(
                context=context,
                content_hash=config_content_hash(config),
                lab_generation=1,
                sample=saved.entry.source.context.sample,
                overrides=overrides,
            ),
            value_origins=context_value_origins(
                config,
                base=saved.config.parameter_snapshot,
                base_ref=context,
                selected_ref=context,
                inherited=saved.entry.source.context.value_origins,
                overrides=overrides,
            ),
        )

    def save_context(
        self,
        *,
        entry_id: str,
        base: ConfigContextRef,
        sample: SampleSelector,
        working_point_id: str,
        label: str,
        parameters: ParameterSnapshot | None = None,
        note: str = "",
    ) -> ConfigEntryView:
        assert sample.revision == self.sample.revision
        saved = save_config_context(
            entry_id=entry_id,
            base=base,
            sample=self.sample,
            working_point_id=working_point_id,
            label=label,
            parameters=parameters,
            note=note,
            actor="operator",
            unit_of_work=self.uow,
        )
        return ConfigEntryView(entry=saved.entry, config=saved.config)


@pytest.fixture
def operations(tmp_path: Path) -> RegistryOperations:
    operations = RegistryOperations(tmp_path)
    config = load_config()
    table = ParameterDefinition(
        id="qubits",
        value_type=Table(
            columns=(
                TableColumn("id", Scalar(String())),
                TableColumn("frequency", Scalar(Float())),
                TableColumn("amplitude", Scalar(Float())),
            ),
            primary_key=("id",),
        ),
    )
    config = config.model_copy(
        update={
            "system": config.system.model_copy(
                update={
                    "parameter_catalog": ParameterCatalog(
                        id="catalog",
                        definitions=(*config.parameter_catalog.definitions, table),
                    )
                }
            ),
            "parameter_snapshot": ParameterSnapshot(
                id="parameters",
                values=(
                    *config.parameter_snapshot.values,
                    TableParameterValue(
                        id="qubits",
                        rows=({"id": "q0", "frequency": 5.0, "amplitude": 0.1},),
                    ),
                ),
            ),
        }
    )
    seed = publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(config), entry_id="lab", actor="operator"
        ),
        unit_of_work=operations.uow,
        expected_generation=0,
    )
    operations.save_context(
        entry_id="start",
        base=ConfigContextRef(entry_id="lab", content_hash=seed.entry.content_hash),
        sample=SampleSelector(sample_id="sample", revision=1),
        working_point_id="parked",
        label="start",
    )
    return operations


def test_edit_freeze_save_reopen_preserves_origins_and_default(
    operations: RegistryOperations,
    tmp_path: Path,
) -> None:
    params = ParameterWorkspace(operations, context="start")
    row = params["qubits"]["q0"]
    row["frequency"] = 5.2
    assert [(edit.field, edit.before, edit.after) for edit in params.diff()] == [
        ("frequency", 5.0, 5.2)
    ]
    frozen = params.freeze()
    row["frequency"] = 5.3
    stored = frozen.config.parameter_snapshot.get("qubits")
    assert isinstance(stored, TableParameterValue)
    assert stored.rows[0]["frequency"] == 5.2
    assert frozen.config_source.sample.revision == 1
    assert frozen.config_source.sample.context_id == "parked"
    origins = {
        item.field_id: item
        for item in frozen.value_origins
        if item.parameter_id == "qubits"
    }
    assert origins["amplitude"].entry.entry_id == "lab"
    assert origins["frequency"].layer == "run_override"
    version = params.save("edited")
    assert params.diff() == ()
    row["frequency"] = 5.4
    params.discard()
    assert row["frequency"] == 5.3
    reopened = ParameterWorkspace(RegistryOperations(tmp_path), context=version)
    assert reopened["qubits"]["q0"]["frequency"] == 5.3
    assert (
        load_active_config_registry_snapshot(unit_of_work=operations.uow).entry.id
        == "lab"
    )
    saved_origins = {
        item.field_id: item
        for item in reopened.freeze().value_origins
        if item.parameter_id == "qubits"
    }
    assert saved_origins["amplitude"].entry.entry_id == "lab"
    assert saved_origins["frequency"].entry.entry_id == "edited"


def test_independent_cell_rebase_and_conflict_are_atomic(
    operations: RegistryOperations,
) -> None:
    first = ParameterWorkspace(operations, context="start")
    second = ParameterWorkspace(operations, context="start")
    first["qubits"]["q0"]["frequency"] = 5.2
    current = first.save("first")
    second["qubits"]["q0"]["amplitude"] = 0.2
    second.rebase(current=current)
    assert dict(second["qubits"]["q0"]) == {
        "id": "q0",
        "frequency": 5.2,
        "amplitude": 0.2,
    }
    second.save("combined")
    conflict = ParameterWorkspace(operations, context="start")
    conflict["qubits"]["q0"]["frequency"] = 5.4
    before = conflict.diff()
    with pytest.raises(Conflict) as caught:
        conflict.rebase(current=current)
    assert conflict.diff() == before
    details = caught.value.problems[0].details
    assert details["column_id"] == "frequency"
    assert "frequency" in str(caught.value)
    assert "base=5.0, local=5.4, current=5.2" in str(caught.value)
    assert all(
        name in details for name in ("base_value", "local_value", "current_value")
    )


def test_create_delete_copy_and_live_row_identity(
    operations: RegistryOperations,
) -> None:
    params = ParameterWorkspace(operations, context="start")
    params["qubits"]["q1"] = {"frequency": 6.0, "amplitude": 0.3}
    detached = params.copy()
    detached["qubits"]["q1"]["frequency"] = 6.1
    assert params["qubits"]["q1"]["frequency"] == 6.0
    row = params["qubits"]["q0"]
    del params["qubits"]["q0"]
    params["qubits"]["q0"] = {"frequency": 5.7, "amplitude": 0.4}
    with pytest.raises(KeyError, match="deleted"):
        row["frequency"]
    del params["qubits"]["q0"]
    version = params.save("different-rows")
    assert list(ParameterWorkspace(operations, context=version)["qubits"]) == ["q1"]


def test_invalid_edits_remain_editable_and_report_fields(
    operations: RegistryOperations,
) -> None:
    params = ParameterWorkspace(operations, context="start")
    with pytest.raises(ValueError, match=r"omits fields.*amplitude"):
        params["qubits"]["q0"] = {"frequency": 5.2}
    params["qubits"]["q0"]["frequency"] = "not a frequency"
    copied = params.copy()
    assert copied["qubits"]["q0"]["frequency"] == "not a frequency"
    with pytest.raises(ValueError, match="qubits"):
        params.preview()
    params.discard()
    assert params.diff() == ()
    with pytest.raises(ValueError, match="row key"):
        params["qubits"]["q0"]["id"] = "q1"


def test_scalar_edits_and_unchanged_named_copy(operations: RegistryOperations) -> None:
    params = ParameterWorkspace(operations, context="start")
    original = params.scalar("drive_frequency")
    params.set_scalar("drive_frequency", Quantity(5.2, "GHz"))
    version = params.save("scalar")
    reopened = ParameterWorkspace(operations, context=version)
    assert reopened.scalar("drive_frequency") == Quantity(5.2, "GHz")
    assert original != reopened.scalar("drive_frequency")
    assert reopened.save("copy").name == "copy"
    with pytest.raises(ValueError, match="Existing versions are immutable"):
        reopened.save("start")


def test_boolean_edit_is_not_hidden_by_python_numeric_equality(
    operations: RegistryOperations,
) -> None:
    params = ParameterWorkspace(operations, context="start")
    params["qubits"]["q0"]["frequency"] = 1.0
    params.save("one")
    params["qubits"]["q0"]["frequency"] = True
    assert len(params.diff()) == 1
    with pytest.raises(ValueError, match="qubits"):
        params.preview()


def test_rebase_retains_other_editors_origins_and_rejects_sample_change(
    operations: RegistryOperations,
) -> None:
    first = ParameterWorkspace(operations, context="start")
    second = ParameterWorkspace(operations, context="start")
    first["qubits"]["q0"]["frequency"] = 5.4
    version = first.save("frequency")
    second["qubits"]["q0"]["amplitude"] = 0.4
    second.rebase(current=version)
    origins = {
        item.field_id: item
        for item in second.freeze().value_origins
        if item.parameter_id == "qubits"
    }
    assert origins["frequency"].entry.entry_id == "frequency"
    assert origins["amplitude"].layer == "run_override"
    before = second.diff()
    operations.sample = operations.sample.model_copy(update={"revision": 2})
    foreign = operations.save_context(
        entry_id="new-sample-revision",
        base=version.context,
        sample=SampleSelector(sample_id="sample", revision=2),
        working_point_id="parked",
        label="changed sample",
    )
    with pytest.raises(ValueError, match="same exact sample revision"):
        second.rebase(current=foreign.entry.id)
    assert second.diff() == before


def test_compatible_unit_read_and_rebase_do_not_rewrite_origins(
    operations: RegistryOperations,
) -> None:
    first = ParameterWorkspace(operations, context="start")
    first.set_scalar("drive_frequency", Quantity(5200, "MHz"))
    version = first.save("megahertz")
    second = ParameterWorkspace(operations, context=version)
    assert second.scalar("drive_frequency") == Quantity(5200, "MHz")
    assert second.diff() == ()
    second["qubits"]["q0"]["amplitude"] = 0.3
    frozen = second.freeze()
    origin = next(
        item for item in frozen.value_origins if item.parameter_id == "drive_frequency"
    )
    assert origin.entry.entry_id == "megahertz"
    scalar = frozen.config.parameter_snapshot.get("drive_frequency")
    assert scalar is not None
    assert scalar.model_dump(mode="json")["value"] == {"value": 5200.0, "unit": "MHz"}


def test_entity_keys_accept_plain_ids_without_rewriting_stored_keys() -> None:
    schema = Table(
        columns=(
            TableColumn("qubit", Scalar(Entity(entity_kind="qubit"))),
            TableColumn("gain", Scalar(Float())),
        ),
        primary_key=("qubit",),
    )
    data = _TableData("drive", schema)
    key = EntityRef(id="q0", kind="qubit")
    data.load(({"qubit": key, "gain": 0.1},))
    table = ParameterTable(data)
    assert table["q0"]["qubit"] == key
    table["q0"] = {"qubit": "q0", "gain": 0.2}
    assert table[key]["gain"] == 0.2
    assert table["q0"]["qubit"] == key
    table["q1"] = {"gain": 0.3}
    assert table["q1"]["qubit"] == EntityRef(id="q1", kind="qubit")
    del table["q1"]
    assert len(table) == 1


def test_typed_rows_save_reopen_and_discard_share_workspace(
    operations: RegistryOperations,
) -> None:
    from dataclasses import dataclass

    @dataclass
    class Qubit:
        id: str
        frequency: float
        amplitude: float | None = 0.25

    params = ParameterWorkspace(operations, context="start")
    before = params.freeze()
    table = params.table("qubits", row_type=Qubit)
    q0 = table["q0"]
    assert q0.frequency == params["qubits"]["q0"]["frequency"]
    assert params.diff() == ()
    assert params.freeze().value_origins == before.value_origins
    q0.frequency = 5.4
    frozen = params.freeze()
    saved = params.save("typed-edits")
    reopened = ParameterWorkspace(operations, context=saved)
    assert reopened.table("qubits", row_type=Qubit)["q0"].frequency == 5.4
    q0.frequency = 5.8
    assert params["qubits"]["q0"]["frequency"] == 5.8
    assert (
        frozen.config.parameter_snapshot == reopened.freeze().config.parameter_snapshot
    )
    params.discard()
    assert q0.frequency == 5.4
