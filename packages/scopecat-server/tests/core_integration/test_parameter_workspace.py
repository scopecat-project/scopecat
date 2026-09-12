"""Author edits use real durable registry entries, with no daemon or devices."""

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import pytest
from scopecat.api.parameters import ParameterTable, ParameterWorkspace, _TableData
from scopecat.authoring.parameter_dataclasses import ParameterSpec
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
from scopecat.config.structure import ParameterStructurePlan
from scopecat.daemon.views import ConfigContextResolution, ConfigEntryView
from scopecat.kernel.entity import EntityRef
from scopecat.kernel.errors import CheckFailed, Conflict
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
        structure_plan: ParameterStructurePlan | None = None,
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
            structure_plan=structure_plan,
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
    original = params.scalars["drive_frequency"]
    params.scalars["drive_frequency"] = Quantity(5.2, "GHz")
    version = params.save("scalar")
    reopened = ParameterWorkspace(operations, context=version)
    assert reopened.scalars["drive_frequency"] == Quantity(5.2, "GHz")
    assert original != reopened.scalars["drive_frequency"]
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
    first.scalars["drive_frequency"] = Quantity(5200, "MHz")
    version = first.save("megahertz")
    second = ParameterWorkspace(operations, context=version)
    assert second.scalars["drive_frequency"] == Quantity(5200, "MHz")
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


# These declarations are ordinary lab-author code. Defaults only construct new rows.


@dataclass
class ProbeParameters:
    id: str
    duration: Annotated[float, ParameterSpec(unit="ns")]
    pi_amplitude: float | None = None


@dataclass
class ExtendedProbeParameters(ProbeParameters):
    quality: float | None = 0.99


def test_declare_complete_unknown_table_save_reopen_and_add_optional_column(
    operations: RegistryOperations,
) -> None:
    params = ParameterWorkspace(operations, context="start")
    probes = params.declare_table("probes", ProbeParameters, key="id")
    probes.add(ProbeParameters("q0", 40))
    assert dict(params["probes"]["q0"]) == {
        "id": "q0",
        "duration": Quantity(40, "ns"),
        "pi_amplitude": None,
    }
    structural = params.structure_diff()
    assert structural is not None
    assert structural.impacts[0].kind == "table_added"
    with pytest.raises(ValueError, match="save a named version"):
        params.freeze()
    detached = params.copy()
    assert dict(detached["probes"]["q0"]) == dict(params["probes"]["q0"])
    version = params.save("unknown-probes")
    assert params.structure_diff() is None
    assert not params.diff()
    reopened = ParameterWorkspace(operations, context=version)
    declared = reopened.declare_table("probes", ProbeParameters, key="id")
    assert declared["q0"].pi_amplitude is None
    assert reopened.structure_diff() is None  # Identical notebook re-execution.
    extended = reopened.declare_table("probes", ExtendedProbeParameters, key="id")
    assert extended["q0"].quality is None  # Never hydrate the initializer default.
    assert reopened["probes"]["q0"]["quality"] is None
    evolved = reopened.save("with-quality")
    before = operations.resolve_context(version.context)
    after = operations.resolve_context(evolved.context)
    old_duration = next(
        o
        for o in before.value_origins
        if o.parameter_id == "probes" and o.field_id == "duration"
    )
    new_duration = next(
        o
        for o in after.value_origins
        if o.parameter_id == "probes" and o.field_id == "duration"
    )
    assert new_duration.entry == old_duration.entry
    assert "quality" not in params["probes"]["q0"]


def test_clearing_one_cell_preserves_other_origins_and_frozen_override(
    operations: RegistryOperations,
) -> None:
    params = ParameterWorkspace(operations, context="start")
    original = params.freeze()
    params["qubits"]["q0"]["amplitude"] = None
    frozen = params.freeze()
    override = frozen.config_source.overrides[0]
    from scopecat.config.parameter_updates import UpdateParameterRows

    assert isinstance(override, UpdateParameterRows)
    assert dict(override.values) == {"amplitude": None}
    assert len(params.diff()) == 1
    assert next(o for o in frozen.value_origins if o.field_id == "frequency") == next(
        o for o in original.value_origins if o.field_id == "frequency"
    )
    assert (
        next(o for o in frozen.value_origins if o.field_id == "amplitude").layer
        == "run_override"
    )
    params["qubits"]["q0"]["amplitude"] = 0.2
    stored = frozen.config.parameter_snapshot.get("qubits")
    assert isinstance(stored, TableParameterValue)
    assert "amplitude" not in stored.rows[0]
    params["qubits"]["q0"]["amplitude"] = None
    version = params.save("clear-amplitude")
    reopened = ParameterWorkspace(operations, context=version)
    assert reopened["qubits"]["q0"]["amplitude"] is None
    origins = reopened.freeze().value_origins
    assert next(o for o in origins if o.field_id == "frequency") == next(
        o for o in original.value_origins if o.field_id == "frequency"
    )
    assert (
        next(o for o in origins if o.field_id == "amplitude").entry == version.context
    )


def test_explicit_rename_unit_and_key_preserve_history_and_addresses(
    operations: RegistryOperations,
) -> None:
    params = ParameterWorkspace(operations, context="start")
    probes = params.declare_table("probes", ProbeParameters, key="id")
    probes.add(ProbeParameters("q0", 40))
    initial = params.save("probe-schema")
    live = params.table("probes", row_type=ProbeParameters)["q0"]
    params.convert_unit("probes", "duration", "us")
    params.rename_column("probes", "duration", "pulse_length")
    params.rename_column("probes", "id", "qubit")
    params.change_key("probes", key="qubit")
    with pytest.raises(KeyError, match="deleted"):
        _ = live.duration
    assert params["probes"]["q0"]["pulse_length"] == Quantity(0.04, "us")
    changed = params.save("probe-renamed")
    origin = next(
        o
        for o in params.freeze().value_origins
        if o.parameter_id == "probes" and o.field_id == "pulse_length"
    )
    assert origin.source_cell is not None
    assert origin.source_cell.entry == initial.context
    assert origin.source_cell.field_id == "duration"
    assert origin.source_cell.key == {"id": "q0"}
    old = ParameterWorkspace(operations, context=initial)
    assert old["probes"]["q0"]["duration"] == Quantity(40, "ns")
    restored = ParameterWorkspace(operations, context=changed)
    restored["probes"]["q0"]["pi_amplitude"] = 0.5
    restored.save("new-amplitude")
    assert (
        next(
            o
            for o in restored.freeze().value_origins
            if o.parameter_id == "probes" and o.field_id == "pulse_length"
        )
        == origin
    )


def test_bootstrap_validation_and_direct_registry_allow_unknown_but_reject_invalid(
    operations: RegistryOperations,
) -> None:
    from scopecat.config.resolution import validate_config_profile
    from scopecat.kernel.errors import CheckFailed

    params = ParameterWorkspace(operations, context="start")
    params["qubits"]["q0"]["amplitude"] = None
    complete = params.freeze().config
    assert validate_config_profile(complete) == complete
    published = publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(complete),
            entry_id="bootstrap-with-unknown",
            actor="operator",
        ),
        unit_of_work=operations.uow,
        expected_generation=1,
    )
    assert published.entry.content_hash == config_content_hash(complete)
    # Absence does not relax provided cell types or physical row key completeness.
    for invalid in ({"id": "q0", "frequency": "bad"}, {"frequency": 5.0}):
        values = ParameterSnapshot(
            id=complete.parameter_snapshot.id,
            values=tuple(
                TableParameterValue(id="qubits", rows=(invalid,))
                if value.id == "qubits"
                else value
                for value in complete.parameter_snapshot.values
            ),
        )
        with pytest.raises(CheckFailed):
            validate_config_profile(
                complete.model_copy(update={"parameter_snapshot": values})
            )


def test_notebook_workspace_views_keep_saved_origins_and_context(
    operations: RegistryOperations,
) -> None:
    workspace = ParameterWorkspace(operations, context="start")
    assert "sample" in repr(workspace) and "parked" in repr(workspace)
    table = workspace["qubits"]
    before = table.render_html()
    assert "Saved ·" in before
    table["q0"]["amplitude"] = 0.0
    assert "Manual · unsaved" in table.render_html()
    assert "Unknown" not in repr(table["q0"])
    table["q0"]["amplitude"] = None
    assert "Unknown" in table.render_html()
    assert "Unknown" in repr(table["q0"])
    assert "Saved ·" in table.render_html()  # Other cells retain provenance.
    assert "sha256" not in repr(workspace)


def test_external_parameter_edits_preserve_other_cell_origins_and_exact_context(
    operations: RegistryOperations,
) -> None:
    import json

    params = ParameterWorkspace(operations, context="start")
    target = params["qubits"]
    document = json.loads(target.export_json())
    document["rows"][0]["frequency"] = 5.25
    preview = target.preview_json(json.dumps(document))
    assert [(edit.field, edit.after) for edit in preview.diff] == [("frequency", 5.25)]
    assert params.diff() == ()
    preview.apply()
    frozen = params.freeze()
    version = params.save("imported-values", note="Reviewed external parameter edit")
    assert frozen.config_source.context.entry_id == "start"
    reopened = ParameterWorkspace(operations, context=version)
    origins = {
        item.field_id: item
        for item in reopened.freeze().value_origins
        if item.parameter_id == "qubits"
    }
    assert origins["amplitude"].entry.entry_id == "lab"
    assert origins["frequency"].entry.entry_id == "imported-values"
    assert (
        load_active_config_registry_snapshot(unit_of_work=operations.uow).entry.id
        == "lab"
    )
    with pytest.raises(ValueError, match="stale"):
        reopened["qubits"].preview_json(json.dumps(document))
    unchanged = reopened["qubits"].preview_json(reopened["qubits"].export_json())
    reopened.save("another-version")
    with pytest.raises(ValueError, match="changed after preview"):
        unchanged.apply()


def test_dynamic_declarations_reopen_and_adopt_model_without_migration(
    operations: RegistryOperations,
) -> None:
    from typing import Literal

    import scopecat as sc

    params = ParameterWorkspace(operations, context="start")
    dynamic = params.declare_table(
        "exploration",
        key="qubit",
        columns={
            "qubit": str,
            "duration": sc.column(float, unit="ns", minimum=4),
            "shape": Literal["constant", "gaussian"],
        },
    )
    dynamic["001"] = {"duration": sc.Quantity(64, "ns"), "shape": "constant"}
    with pytest.raises(ValueError, match="Save or discard"):
        params.add_column("exploration", "amplitude", float | None)
    params.save("dynamic-start")
    params.add_column("exploration", "amplitude", float | None)
    params.declare_scalar("attempts", sc.column(int, minimum=1), value=3)
    structure = params.structure_diff()
    assert structure is not None
    assert {impact.kind for impact in structure.impacts} == {"added", "scalar_added"}
    # The same wire plan survives transport, including explicit scalar values.
    plan = params._structure_plan()
    assert plan is not None
    assert ParameterStructurePlan.model_validate_json(plan.model_dump_json()) == plan
    version = params.save("dynamic-complete")
    reopened = ParameterWorkspace(operations, context=version)
    assert reopened["exploration"]["001"]["amplitude"] is None
    assert reopened.scalars["attempts"] == 3
    initial_origin = next(
        item
        for item in reopened.freeze().value_origins
        if item.parameter_id == "attempts"
    )
    assert initial_origin.entry.entry_id == version.name
    assert initial_origin.layer == "context"
    assert initial_origin.evidence is None

    class Exploration(sc.ParameterModel, table="exploration"):
        qubit: sc.Param[str] = sc.param(key=True)
        duration: sc.Magnitude[float] = sc.quantity(unit="ns", minimum=4)
        shape: sc.Param[Literal["constant", "gaussian"]] = sc.param()
        amplitude: sc.Param[float | None] = sc.param(default=0.5)

    typed = reopened[Exploration]
    assert typed["001"].duration == 64
    assert typed["001"].amplitude is None
    typed["001"].duration = 80
    reopened.scalars["attempts"] = 4
    final = reopened.save("dynamic-edited")
    restored = ParameterWorkspace(operations, context=final)
    assert restored[Exploration]["001"].duration == 80
    assert restored.scalars["attempts"] == 4
    assert any(
        o.parameter_id == "attempts" and o.entry.entry_id == final.name
        for o in restored.freeze().value_origins
    )
    assert ParameterWorkspace(operations, context="dynamic-start")["exploration"][
        "001"
    ]["duration"] == sc.Quantity(64, "ns")
    assert (
        load_active_config_registry_snapshot(unit_of_work=operations.uow).entry.id
        == "lab"
    )


def test_dynamic_structure_rejections_leave_the_draft_unchanged(
    operations: RegistryOperations,
) -> None:
    import scopecat as sc

    params = ParameterWorkspace(operations, context="start")
    with pytest.raises(TypeError, match="Optional"):
        params.declare_table("bad", key="id", columns={"id": str | None})
    with pytest.raises(TypeError, match="optional"):
        params.add_column("qubits", "quality", float)
    with pytest.raises(ValueError, match="already exists"):
        params.declare_scalar("drive_frequency", float, value=3.0)
    before = params.freeze()
    with pytest.raises(CheckFailed, match="invalid"):
        params.declare_scalar("invalid", sc.column(int, minimum=1), value=0)
    assert params.freeze() == before
    assert "invalid" not in params.scalars


def test_dynamic_references_keep_schema_without_reading_unknown_values(
    operations: RegistryOperations,
) -> None:
    from typing import assert_type

    import scopecat as sc
    from scopecat.program.value_refs import internal_value_ref_parameter_lookup

    params = ParameterWorkspace(operations, context="start")
    table = params.declare_table(
        "pairs",
        key=("left", "right"),
        columns={
            "left": str,
            "right": str,
            "duration": sc.column(float | None, unit="ns"),
        },
    )
    table[("q0", "q1")] = {"duration": None}
    ref = table.ref("duration", ("q0", "q1"), as_type=sc.Quantity)
    assert_type(ref, sc.ValueRef[sc.Quantity])
    locator = internal_value_ref_parameter_lookup(ref)
    assert locator is not None
    assert dict(locator[1]) == {"left": "q0", "right": "q1"}
    assert locator[0].result_type.atom == sc.QuantityType(unit="ns")
    with pytest.raises(TypeError, match="Quantity"):
        table.ref("duration", ("q0", "q1"), as_type=float)
    with pytest.raises(ValueError, match="expected keys"):
        table.ref("duration", "q0")
    params.save("pair-start")
    params.convert_unit("pairs", "duration", "us")
    new = internal_value_ref_parameter_lookup(
        params["pairs"].ref("duration", ("q0", "q1"))
    )
    assert new is not None
    assert new[0].result_type.atom == sc.QuantityType(unit="us")
    assert locator[0].result_type.atom == sc.QuantityType(unit="ns")
    assert params["pairs"][("q0", "q1")]["duration"] is None
