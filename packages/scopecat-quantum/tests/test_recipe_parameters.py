from typing import Annotated

import pytest
import scopecat as sc
from scopecat.authoring.parameter_models import parameter_catalog, parameter_snapshot
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.kernel.value_validation import ValueValidationError
from scopecat.records.parameter import TableParameterValue

from scopecat_quantum import authoring as q
from scopecat_quantum.recipe_parameters import (
    RecipeParameterEvidence,
    recipe_parameter_input_ids,
    resolve_recipe_parameters,
)


class Drive(sc.ParameterModel, table="drive"):
    qubit: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="ns", minimum=0, default=16)
    beta: sc.Param[float | None] = sc.param(default=None)


@q.program
def acquire(target: q.Qubit):
    return q.measure(target, result="iq")


def snapshot():
    return parameter_snapshot("baseline", tables={Drive: [Drive(qubit="q0")]})


def resolve(call, values):
    return resolve_recipe_parameters(
        call.program,
        dict(zip(recipe_parameter_input_ids(call.program), values, strict=True)),
        catalog=parameter_catalog("catalog", Drive),
        base=snapshot(),
    )


def test_point_values_create_independent_scopes_without_mutating_baseline():
    call = acquire("q0").with_recipe_parameters(
        "candidate", q.recipe_parameter(Drive.duration, "q0", 24)
    )
    input_id = call.program.recipe_parameter_bindings[0].input_id
    original = snapshot()
    for duration in (24, 16, 32):
        result = resolve_recipe_parameters(
            call.program,
            {input_id: sc.Quantity(duration, "ns")},
            catalog=parameter_catalog("catalog", Drive),
            base=original,
        )
        [table] = result.scoped_parameters["candidate"].values
        assert isinstance(table, TableParameterValue)
        assert table.rows[0]["duration"] == sc.Quantity(duration, "ns")
        [evidence] = result.evidence
        assert evidence.base_snapshot_id == "baseline"
        assert (evidence.base_fingerprint == evidence.scope_fingerprint) == (
            duration == 16
        )
        assert (
            RecipeParameterEvidence.model_validate_json(evidence.model_dump_json())
            == evidence
        )
    assert original == snapshot()


def test_shots_and_device_inputs_preserve_recipe_bindings_and_call_identity():
    original = acquire("q0").with_compiler_inputs(cycle_period=sc.Quantity(1, "us"))
    candidate = original.with_recipe_parameters(
        "candidate", q.recipe_parameter(Drive.duration, "q0", 24)
    )
    rebound = candidate.with_shots(16).with_compiler_inputs(
        cycle_period=sc.Quantity(2, "us")
    )
    assert (
        rebound.program.recipe_parameter_bindings
        == candidate.program.recipe_parameter_bindings
    )
    assert rebound.domain_call.key == original.domain_call.key
    assert set(dict(rebound.compiler_arguments)) == {
        "cycle_period",
        *recipe_parameter_input_ids(rebound.program),
    }
    assert candidate.program.recipe_parameter_bindings[0].input_id in dict(
        candidate.compiler_arguments
    )
    assert original.program.recipe_parameter_bindings == ()


def test_multiple_scopes_start_from_baseline_and_fill_unknown_cells():
    call = (
        acquire("q0")
        .with_recipe_parameters("whole", q.recipe_parameter(Drive.duration, "q0", 24))
        .with_recipe_parameters("inserted", q.recipe_parameter(Drive.beta, "q0", 0.2))
    )
    bindings = call.program.recipe_parameter_bindings
    result = resolve_recipe_parameters(
        call.program,
        {bindings[0].input_id: sc.Quantity(24, "ns"), bindings[1].input_id: 0.2},
        catalog=parameter_catalog("catalog", Drive),
        base=snapshot(),
    )
    whole, inserted = (
        result.scoped_parameters[name].values[0] for name in ("whole", "inserted")
    )
    assert isinstance(whole, TableParameterValue)
    assert isinstance(inserted, TableParameterValue)
    assert "beta" not in whole.rows[0]
    assert inserted.rows[0]["duration"] == sc.Quantity(16, "ns")
    assert inserted.rows[0]["beta"] == 0.2


def test_symbolic_candidate_survives_experiment_graph():
    @sc.experiment
    def experiment(
        context: sc.ExperimentContext,
        duration: Annotated[sc.Quantity, sc.QuantityType(unit="ns", minimum=0)],
    ):
        call = acquire("q0").with_recipe_parameters(
            "candidate", q.recipe_parameter(Drive.duration, "q0", duration)
        )
        context.use(call)
        return call.results.iq

    built = compile_invocation(experiment.build(sc.Quantity(24, "ns")))
    [execution] = built.program.program.domain_executions
    assert len(execution.compiler_inputs) == 1
    assert execution.program.body.recipe_parameter_bindings[0].table == "drive"


@pytest.mark.parametrize("value", [sc.Quantity(-1, "ns"), sc.Quantity(1, "GHz")])
def test_candidate_rejects_invalid_units_or_ranges(value):
    with pytest.raises(ValueError, match=r"convert|minimum|least|greater"):
        q.recipe_parameter(Drive.duration, "q0", value)
    call = acquire("q0").with_recipe_parameters(
        "candidate", q.recipe_parameter(Drive.duration, "q0", 24)
    )
    with pytest.raises(ValueValidationError):
        resolve(call, [value])


def test_candidates_reject_missing_rows_duplicate_cells_and_input_collisions():
    missing = acquire("q0").with_recipe_parameters(
        "candidate", q.recipe_parameter(Drive.duration, "missing", 24)
    )
    with pytest.raises(ValueError, match="no row"):
        resolve(missing, [sc.Quantity(24, "ns")])
    candidate = acquire("q0").with_recipe_parameters(
        "candidate", q.recipe_parameter(Drive.duration, "q0", 24)
    )
    with pytest.raises(ValueError, match="duplicate"):
        candidate.with_recipe_parameters(
            "candidate", q.recipe_parameter(Drive.duration, "q0", 32)
        )
    with pytest.raises(ValueError, match="owned"):
        candidate.with_compiler_inputs(
            **{candidate.program.recipe_parameter_bindings[0].input_id: 0}
        )
