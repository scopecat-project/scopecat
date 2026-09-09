"""Context persistence and provenance use the ordinary registry, without devices."""

from functools import partial
from pathlib import Path

import pytest
from scopecat.config.contexts import (
    apply_context_overrides,
    context_value_origins,
    validate_context_config,
)
from scopecat.config.parameter_updates import ReplaceParameter
from scopecat.config.registry import (
    ConfigRevision,
    DirectConfigRevisionSource,
    load_active_config_registry_snapshot,
    publish_config_revision,
)
from scopecat.config.registry.records import ContextConfigRegistrySource
from scopecat.config.registry.service import (
    ConfigRegistryEntrySnapshot,
    save_config_context,
)
from scopecat.kernel.errors import CheckFailed
from scopecat.kernel.quantity import Quantity
from scopecat.kernel.value_types import Float, Scalar, Table, TableColumn
from scopecat.records.config import config_content_hash
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
    TableParameterValue,
)
from scopecat.records.sample import SampleBinding
from scopecat_testkit.config_registry import load_config
from scopecat_testkit.server.runtime import sqlite_config_registry_unit_of_work


def test_two_samples_two_working_points_are_saved_without_activation(
    tmp_path: Path,
) -> None:
    uow = sqlite_config_registry_unit_of_work(tmp_path)
    base = load_config()
    seed = publish_config_revision(
        revision=ConfigRevision(
            source=DirectConfigRevisionSource(base), entry_id="lab", actor="operator"
        ),
        unit_of_work=uow,
        expected_generation=0,
    )
    base_ref = ConfigContextRef(
        entry_id=seed.entry.id, content_hash=seed.entry.content_hash
    )
    saved: list[ConfigRegistryEntrySnapshot] = []
    for index, (sample, point) in enumerate(
        (("a", "parked"), ("a", "shifted"), ("b", "parked"), ("b", "shifted"))
    ):
        parameters = ParameterSnapshot(
            id=base.parameter_snapshot.id,
            values=(
                ScalarParameterValue(
                    id="drive_frequency", value=Quantity(4.8 + index / 10, "GHz")
                ),
            ),
        )
        command = partial(
            save_config_context,
            entry_id=f"{sample}-{point}",
            base=base_ref,
            sample=SampleBinding(
                role="subject",
                sample_id=sample,
                revision=1,
                content_hash="sha256:" + "a" * 64,
                kind="synthetic",
                display_name=sample,
                context_id=point,
            ),
            working_point_id=point,
            label=f"{sample} / {point}",
            parameters=parameters,
            actor="operator",
            note="trial",
            unit_of_work=uow,
        )
        entry = command()
        assert command().entry == entry.entry
        assert isinstance(entry.entry.source, ContextConfigRegistrySource)
        assert entry.entry.source.context.sample.sample_id == sample
        saved.append(entry)
    assert (
        load_active_config_registry_snapshot(unit_of_work=uow).activation
        == seed.activation
    )
    assert len({item.entry.id for item in saved}) == 4
    assert len({item.entry.content_hash for item in saved}) == 4


def test_partial_context_validates_present_values_and_can_fill_unknown() -> None:
    base = load_config()
    partial = base.model_copy(
        update={"parameter_snapshot": ParameterSnapshot(id="unknown")}
    )
    validate_context_config(partial)
    filled = apply_context_overrides(
        partial,
        (
            ReplaceParameter(
                value=ScalarParameterValue(
                    id="drive_frequency", value=Quantity(5100, "MHz")
                )
            ),
        ),
    )
    assert filled.parameter_snapshot.get("drive_frequency") is not None
    with pytest.raises(CheckFailed):
        apply_context_overrides(
            partial,
            (
                ReplaceParameter(
                    value=ScalarParameterValue(
                        id="drive_frequency", value=Quantity(1, "s")
                    )
                ),
            ),
        )
    assert partial.parameter_snapshot.values == ()


def test_unkeyed_table_origins_retain_distinct_rows_after_copy() -> None:
    base = load_config()
    definition = ParameterDefinition(
        id="values", value_type=Table(columns=(TableColumn("value", Scalar(Float())),))
    )
    system = base.system.model_copy(
        update={
            "parameter_catalog": ParameterCatalog(
                id="catalog", definitions=(definition,)
            )
        }
    )
    before = ParameterSnapshot(
        id="before",
        values=(
            TableParameterValue(id="values", rows=({"value": 1.0}, {"value": 2.0})),
        ),
    )
    after = ParameterSnapshot(
        id="after",
        values=(
            TableParameterValue(id="values", rows=({"value": 1.0}, {"value": 3.0})),
        ),
    )
    config = base.model_copy(update={"system": system, "parameter_snapshot": after})
    base_ref = ConfigContextRef(entry_id="base", content_hash=config_content_hash(base))
    first_ref = ConfigContextRef(
        entry_id="first", content_hash=config_content_hash(config)
    )
    first = context_value_origins(
        config, base=before, base_ref=base_ref, selected_ref=first_ref
    )
    copied = context_value_origins(
        config,
        base=after,
        base_ref=first_ref,
        selected_ref=ConfigContextRef(
            entry_id="copy", content_hash=first_ref.content_hash
        ),
        inherited=first,
    )
    assert [item.row_index for item in first] == [0, 1]
    assert [item.entry.entry_id for item in first] == ["base", "first"]
    assert copied == first


def test_explicit_equal_trial_values_keep_override_origin_and_typed_roundtrip() -> None:
    from pydantic import TypeAdapter
    from scopecat.config.parameter_updates import ParameterUpdate, UpdateParameterRows
    from scopecat.kernel.value_types import String

    base = load_config()
    scalar = ScalarParameterValue(id="frequency", value=Quantity(5.0, "GHz"))
    table = TableParameterValue(id="cells", rows=({"id": "q0", "value": 1.0},))
    snapshot = ParameterSnapshot(id="values", values=(scalar, table))
    config = base.model_copy(
        update={
            "parameter_snapshot": snapshot,
            "system": base.system.model_copy(
                update={
                    "parameter_catalog": ParameterCatalog(
                        id="catalog",
                        definitions=(
                            ParameterDefinition(
                                id="frequency", value_type=Scalar(Float())
                            ),
                            ParameterDefinition(
                                id="cells",
                                value_type=Table(
                                    columns=(
                                        TableColumn("id", Scalar(String())),
                                        TableColumn("value", Scalar(Float())),
                                    ),
                                    primary_key=("id",),
                                ),
                            ),
                        ),
                    )
                }
            ),
        }
    )
    ref = ConfigContextRef(entry_id="base", content_hash=config_content_hash(config))
    edits: tuple[ParameterUpdate, ...] = (
        ReplaceParameter(value=scalar),
        UpdateParameterRows(
            parameter_id="cells", key={"id": "q0"}, values={"value": 1.0}
        ),
    )
    origins = context_value_origins(
        config, base=snapshot, base_ref=ref, selected_ref=ref, overrides=edits
    )
    assert [(item.parameter_id, item.field_id, item.layer) for item in origins] == [
        ("frequency", None, "run_override"),
        ("cells", "id", "base"),
        ("cells", "value", "run_override"),
    ]
    adapter = TypeAdapter(tuple[ParameterUpdate, ...])
    assert adapter.validate_json(adapter.dump_json(edits)) == edits
    assert ParameterSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot
