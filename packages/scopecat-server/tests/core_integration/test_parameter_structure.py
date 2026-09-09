"""Structure metadata round trips through the existing configuration registry."""

from pathlib import Path

from scopecat.config.contexts import apply_context_overrides, context_value_origins
from scopecat.config.parameter_updates import UpdateParameterRows
from scopecat.config.registry import (
    ConfigRevision,
    DirectConfigRevisionSource,
    publish_config_revision,
)
from scopecat.config.registry.records import ContextConfigRegistrySource
from scopecat.config.registry.service import (
    load_config_registry_entry_snapshot,
    save_config_context,
)
from scopecat.config.structure import (
    ParameterStructurePlan,
    parameter_structure_version,
)
from scopecat.kernel.value_types import Float, Scalar, String, Table, TableColumn
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    TableParameterValue,
)
from scopecat.records.parameter_structure import (
    AddParameterColumn,
    RenameParameterColumn,
    StructureValueDecision,
)
from scopecat.records.sample import SampleBinding
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.runtime import sqlite_config_registry_unit_of_work


def test_structure_save_preserves_source_addresses_and_imported_evidence(
    tmp_path: Path,
) -> None:
    uow = sqlite_config_registry_unit_of_work(tmp_path)
    base = load_config()
    base = base.model_copy(
        update={
            "system": base.system.model_copy(
                update={
                    "parameter_catalog": ParameterCatalog(
                        id=base.parameter_catalog.id,
                        definitions=(
                            *base.parameter_catalog.definitions,
                            ParameterDefinition(
                                id="observations",
                                value_type=Table(
                                    columns=(
                                        TableColumn("specimen", Scalar(String())),
                                    ),
                                    primary_key=("specimen",),
                                ),
                            ),
                        ),
                    )
                }
            ),
            "parameter_snapshot": ParameterSnapshot(
                id=base.parameter_snapshot.id,
                values=(
                    *base.parameter_snapshot.values,
                    TableParameterValue(
                        id="observations", rows=({"specimen": "a"}, {"specimen": "b"})
                    ),
                ),
            ),
        }
    )
    initial = publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(base), entry_id="lab", actor="operator"
        ),
        unit_of_work=uow,
        expected_generation=0,
    )
    ref = ConfigContextRef(
        entry_id=initial.entry.id, content_hash=initial.entry.content_hash
    )
    sample = SampleBinding(
        role="subject",
        sample_id="a",
        revision=1,
        content_hash="sha256:" + "a" * 64,
        kind="synthetic",
        display_name="A",
    )
    plan = ParameterStructurePlan(
        base=ref,
        structure_version=parameter_structure_version(base.parameter_catalog),
        edits=(
            AddParameterColumn(
                parameter_id="observations",
                column=ParameterDefinition(id="quality", value_type=Scalar(Float())),
                values=(
                    StructureValueDecision(
                        key={"specimen": "a"},
                        value=0.8,
                        origin="imported",
                        note="Imported notebook observation",
                    ),
                    StructureValueDecision(
                        key={"specimen": "b"},
                        origin="unknown",
                        note="Sample B has not been evaluated",
                    ),
                ),
            ),
            RenameParameterColumn(
                parameter_id="observations", column_id="specimen", new_id="sample"
            ),
        ),
    )
    saved = save_config_context(
        entry_id="structured",
        base=ref,
        sample=sample,
        working_point_id="parked",
        label="Updated",
        parameters=None,
        structure_plan=plan,
        actor="operator",
        note="Optional column",
        unit_of_work=uow,
    )
    assert (
        load_config_registry_entry_snapshot(entry_id=saved.entry.id, unit_of_work=uow)
        == saved
    )
    assert isinstance(saved.entry.source, ContextConfigRegistrySource)
    origins = saved.entry.source.context.value_origins
    renamed = next(
        item
        for item in origins
        if item.field_id == "sample" and item.key == {"sample": "a"}
    )
    assert renamed.source_cell is not None
    assert renamed.source_cell.entry == ref
    assert renamed.source_cell.field_id == "specimen"
    assert renamed.source_cell.key == {"specimen": "a"}
    quality = next(
        item
        for item in origins
        if item.field_id == "quality" and item.key == {"sample": "a"}
    )
    assert quality.evidence is not None and quality.evidence.origin == "imported"
    copied = save_config_context(
        entry_id="copy",
        base=ConfigContextRef(
            entry_id=saved.entry.id, content_hash=saved.entry.content_hash
        ),
        sample=sample,
        working_point_id="parked",
        label="Copy",
        parameters=None,
        actor="operator",
        note="Copy",
        unit_of_work=uow,
    )
    assert isinstance(copied.entry.source, ContextConfigRegistrySource)
    assert quality in copied.entry.source.context.value_origins
    assert renamed in copied.entry.source.context.value_origins

    unknown = next(
        item
        for item in origins
        if item.field_id == "quality" and item.key == {"sample": "b"}
    )
    assert (
        unknown.evidence is not None
        and unknown.evidence.note == "Sample B has not been evaluated"
    )
    copied_ref = ConfigContextRef(
        entry_id=copied.entry.id, content_hash=copied.entry.content_hash
    )
    resolved_origins = context_value_origins(
        copied.config,
        base=copied.config.parameter_snapshot,
        base_ref=copied_ref,
        selected_ref=copied_ref,
        inherited=copied.entry.source.context.value_origins,
    )
    assert unknown in resolved_origins
    override = UpdateParameterRows(
        parameter_id="observations", key={"sample": "b"}, values={"quality": 0.9}
    )
    filled = apply_context_overrides(copied.config, (override,))
    filled_origins = context_value_origins(
        filled,
        base=copied.config.parameter_snapshot,
        base_ref=copied_ref,
        selected_ref=copied_ref,
        inherited=copied.entry.source.context.value_origins,
        overrides=(override,),
    )
    assert unknown not in filled_origins
    assert (
        next(
            item
            for item in filled_origins
            if item.field_id == "quality" and item.key == {"sample": "b"}
        ).layer
        == "run_override"
    )
