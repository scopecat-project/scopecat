"""Structure revisions remain ordinary contexts, including unknown author columns."""

from __future__ import annotations

import os
from typing import cast

import pytest
import scopecat as sc
from scopecat.config.registry.records import ContextConfigRegistrySource
from scopecat.config.structure import (
    ParameterStructurePlan,
    parameter_structure_version,
)
from scopecat.kernel.errors import CheckFailed
from scopecat.records.config_context import ConfigContextRef
from scopecat.records.parameter import ParameterDefinition
from scopecat.records.parameter_structure import AddParameterColumn
from scopecat.records.sample import SampleRevisionDraft

from reference_lab.application import create_application
from reference_lab.configuration import EXAMPLE_ROOT
from reference_lab.parameters import DRIVE_CARRIER_FREQUENCY, Q0, QUBIT, QUBITS
from reference_lab.workflows.exploratory_signal import exploratory_signal, response

QUALITY = sc.parameter_field("quality", sc.FloatType())
QUALITY_TABLE = sc.parameter_schema(
    "qubits", fields=(QUBIT, QUALITY), primary_key=(QUBIT,)
)
QUALITY_Q0 = QUALITY_TABLE.row(QUBIT.key("q0"))


@sc.experiment(id="reference_lab.structure_quality_signal")
def quality_signal(experiment: sc.ExperimentContext) -> sc.ValueRef[float]:
    frequency = experiment.scan("frequency", (4.8, 4.9), unit="GHz")
    return cast(
        "sc.ValueRef[float]",
        experiment.compute(
            fn=response,
            frequency=frequency,
            center=Q0[DRIVE_CARRIER_FREQUENCY].ref,
            gain=QUALITY_Q0[QUALITY].ref,
        ),
    )


def test_structure_unknown_column_and_old_run_retention() -> None:
    with create_application(EXAMPLE_ROOT).connect(
        os.environ["SCOPECAT_DAEMON_URL"]
    ) as lab:
        active = lab.config.active()
        lab.samples.create(
            "structure-sample",
            kind="synthetic",
            content=SampleRevisionDraft(display_name="Structure sample"),
        )
        base = ConfigContextRef(
            entry_id=active.entry.id, content_hash=active.entry.content_hash
        )
        old_context = lab.config.save_context(
            entry_id="structure-original",
            base=base,
            sample=lab.samples.handle("structure-sample").selector(),
            working_point_id="parked",
            label="Original structure",
        )
        base = ConfigContextRef(
            entry_id=old_context.entry.id, content_hash=old_context.entry.content_hash
        )
        original = lab.run(exploratory_signal(), config=base)
        original_json = original.config.model_dump_json()
        plan = ParameterStructurePlan(
            base=base,
            structure_version=parameter_structure_version(
                active.config.parameter_catalog
            ),
            edits=(
                AddParameterColumn(
                    parameter_id=QUBITS.id,
                    column=ParameterDefinition(
                        id=QUALITY.id, value_type=QUALITY.value_type
                    ),
                ),
            ),
        )
        preview = lab.config.preview_structure(plan)
        assert any(QUALITY.id in path for path in preview.missing_values)
        saved = lab.config.save_context(
            entry_id="structure-with-quality",
            base=base,
            sample=lab.samples.handle("structure-sample").selector(),
            working_point_id="parked",
            label="Optional quality",
            structure_plan=plan,
        )
        assert isinstance(saved.entry.source, ContextConfigRegistrySource)
        assert saved.entry.source.context.structure == preview.origin
        ref = ConfigContextRef(
            entry_id=saved.entry.id, content_hash=saved.entry.content_hash
        )
        assert lab.run(exploratory_signal(), config=ref).status == "completed"
        with pytest.raises((ValueError, KeyError, CheckFailed), match="quality"):
            lab.preview(quality_signal(), config=ref)
        resolved = lab.config.resolve_context(
            ref, overrides=(QUALITY_Q0[QUALITY].update(0.8),)
        )
        assert lab.run(quality_signal(), config=resolved).status == "completed"
        retained = lab.get_run(original.id)
        assert retained.config.model_dump_json() == original_json
        assert lab.config.active() == active
        assert lab.run(exploratory_signal(), config=base).status == "completed"
