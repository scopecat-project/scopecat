from __future__ import annotations

from scopecat_testkit.authoring import bind_invocation, load_config
from scopecat_testkit.domain import domain_call

import scopecat as sc
from scopecat.planning.measurement_projection import (
    project_static_value_record_candidates,
)
from scopecat.planning.point_materialization import prepare_bound_points
from scopecat.program.domain import domain_program
from scopecat.program.products import ModuleProductDecl, ProductValueSpec
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.parameter import TableParameterValue


class ClassifierRow(sc.ParameterModel, table="classifier_rows"):
    device: sc.Param[str] = sc.param(key=True)
    threshold: sc.Param[float] = sc.param()


def _config_with_classifier_rows() -> ConfigProfileSnapshot:
    config = load_config()
    system = config.system.model_copy(
        update={
            "parameter_catalog": config.parameter_catalog.model_copy(
                update={
                    "definitions": (
                        *config.parameter_catalog.definitions,
                        sc.parameter_definition(ClassifierRow),
                    )
                }
            )
        }
    )
    snapshot = config.parameter_snapshot.model_copy(
        update={
            "values": (
                *config.parameter_snapshot.values,
                TableParameterValue(
                    id=sc.parameter_table_name(ClassifierRow),
                    rows=(
                        {"device": "q0", "threshold": 0.25},
                        {"device": "q1", "threshold": 0.5},
                    ),
                ),
            )
        }
    )
    return config.model_copy(update={"system": system, "parameter_snapshot": snapshot})


def _classify(*, signal: object, rows: object) -> float:
    del signal, rows
    return 0.0


def test_measurement_compute_materializes_parameter_table_inputs_per_point() -> None:
    program = domain_program(
        "source",
        dialect_id="test.measurement-projection",
        dialect_version="1",
        body=object(),
        results={"signal": ("signal", "v1")},
    )
    call = domain_call(
        program,
        products={
            "signal": ModuleProductDecl(
                "signal",
                value_spec=ProductValueSpec(dtype="float64"),
            )
        },
    )

    @sc.module(id="test.measurement-projection.table-input")
    def module(context: sc.ModuleContext) -> sc.ProductRef:
        context.use(call)
        result = context.compute(
            "classify",
            fn=_classify,
            signal=call.results.signal,
            rows=sc.parameter_table_ref(ClassifierRow),
            output_type=sc.ScalarType(sc.FloatType()),
        )
        assert isinstance(result, sc.ProductRef)
        return result

    @sc.experiment(id="test.measurement-projection.table-input")
    def experiment(context: sc.ExperimentContext) -> None:
        context.alias(context.use(module()), record_id="classified")

    bound = bind_invocation(
        experiment(),
        config_profile=_config_with_classifier_rows(),
    )
    bound_points = prepare_bound_points(bound)
    [compute] = bound.bindings.measurement_computes
    [table_input] = compute.value_inputs

    [candidate] = project_static_value_record_candidates(
        bound_points,
        (table_input.value_id,),
    )

    assert candidate.value == [
        {"device": "q0", "threshold": 0.25},
        {"device": "q1", "threshold": 0.5},
    ]
