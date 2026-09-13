from __future__ import annotations

import pytest

import scopecat as sc
from scopecat.compiler.frontend.resolution import compile_invocation
from scopecat.kernel.errors import CheckFailed
from scopecat.program.logical import LogicalProgram
from scopecat.program.point_domain import PointAxisValues
from scopecat.program.scans import PointGrouping, PointSchedule
from scopecat.records.run_request import GridDomainRecord, PointCloudDomainRecord

_INT = sc.ScalarType(sc.IntType())


class _Optimizer:
    id = "test.optimizer"

    def propose(
        self,
        context: sc.DomainOptimizerContext,
    ) -> sc.DomainProposalAttempt | sc.OptimizationComplete:
        del context
        return sc.OptimizationComplete()


def _axis_values(program: LogicalProgram, axis_id: str) -> tuple[object, ...]:
    axis = next(axis for axis in program.point_domain if axis.id == axis_id)
    source = axis.source
    assert isinstance(source, PointAxisValues)
    return source.values


def test_compile_projects_the_base_grid_and_composes_the_expanded_plan() -> None:
    frequency = sc.coordinate("frequency", _INT)

    @sc.experiment(id="test.repeat-grid", kind="point_plan")
    def repeated(experiment: sc.ExperimentContext) -> None:
        experiment.grid(
            sc.axis(frequency, (1, 2)),
            repeat=2,
            repeat_mode="sweep",
            traversal="snake",
        )

    compiled = compile_invocation(repeated.build())
    request_plan = compiled.request.point_plan
    program = compiled.program.program

    assert isinstance(request_plan.domain, GridDomainRecord)
    assert [axis.axis_id for axis in request_plan.domain.axes] == ["frequency"]
    assert request_plan.repeat == 2
    assert request_plan.repeat_mode == "sweep"
    assert request_plan.schedule.traversal == "snake"
    assert [axis.id for axis in program.point_domain] == ["repeat", "frequency"]
    assert _axis_values(program, "repeat") == (0, 1)
    assert _axis_values(program, "frequency") == (1, 2)
    assert program.point_repeat == 2
    assert program.point_repeat_mode == "sweep"
    assert program.point_schedule.traversal == "snake"


def test_inferred_scan_declares_paired_execution_rows() -> None:
    @sc.experiment(id="test.paired-scan", kind="point_plan")
    def paired(experiment: sc.ExperimentContext) -> None:
        experiment.scan("detuning", (-1, 0, 1))
        prepared_state = experiment.scan("prepared_state", (0, 1))
        experiment.group_points(
            "prepared-state-comparison",
            varying=(prepared_state,),
        )

    compiled = compile_invocation(paired.build())

    grouping = compiled.request.point_plan.schedule.grouping
    assert grouping is not None
    assert grouping.id == "prepared-state-comparison"
    assert grouping.varying_coordinate_ids == ("prepared_state",)
    assert compiled.program.program.point_schedule == PointSchedule(
        traversal="forward",
        grouping=PointGrouping(
            id="prepared-state-comparison",
            varying_coordinate_ids=("prepared_state",),
        ),
    )
    assert [axis.id for axis in compiled.program.program.point_domain] == [
        "detuning",
        "prepared_state",
    ]
    assert _axis_values(compiled.program.program, "prepared_state") == (0, 1)


def test_point_grouping_is_independent_of_explicit_grid_declaration_order() -> None:
    x = sc.coordinate("x", _INT)

    @sc.experiment(id="test.blocked-grid", kind="point_plan")
    def blocked(experiment: sc.ExperimentContext) -> None:
        experiment.group_points("x-comparison", varying=(x,))
        experiment.grid(sc.axis(x, (0, 1)))

    compiled = compile_invocation(blocked.build())

    assert compiled.request.point_plan.schedule.grouping is not None
    assert compiled.request.point_plan.schedule.grouping.id == "x-comparison"


def test_point_grouping_rejects_a_coordinate_outside_the_point_domain() -> None:
    x = sc.coordinate("x", _INT)
    missing = sc.coordinate("missing", _INT)

    @sc.experiment(id="test.invalid-group-coordinate", kind="point_plan")
    def grouped(experiment: sc.ExperimentContext) -> None:
        experiment.grid(sc.axis(x, (0, 1)))

    invocation = grouped.build().with_point_grouping(
        "invalid-comparison",
        varying=(missing,),
    )

    with pytest.raises(CheckFailed) as error:
        compile_invocation(invocation)

    assert [problem.code for problem in error.value.problems] == [
        "point_grouping_coordinate_missing"
    ]


def test_compile_records_adaptive_policy_without_serializing_optimizer() -> None:
    x = sc.coordinate("x", _INT)

    @sc.experiment(id="test.adaptive-grid", kind="point_plan")
    def adaptive_grid(experiment: sc.ExperimentContext) -> None:
        experiment.grid(sc.axis(x, (0, 1)))

    optimizer = _Optimizer()
    compiled = compile_invocation(
        adaptive_grid.build().adaptive(optimizer, max_points=8, axes=(x,))
    )

    assert compiled.adaptive_domain_plan is not None
    assert compiled.adaptive_domain_plan.optimizer is optimizer
    assert compiled.request.adaptive_domain_plan is not None
    assert compiled.request.adaptive_domain_plan.optimizer_id == "test.optimizer"
    assert compiled.request.adaptive_domain_plan.total_point_limit == 8
    assert compiled.request.adaptive_domain_plan.adaptive_coordinate_ids == ("x",)
    assert "optimizer" not in compiled.request.model_dump(mode="json")


def test_adaptive_point_domain_rejects_recovery_grouping_until_supported() -> None:
    x = sc.coordinate("x", _INT)

    @sc.experiment(id="test.adaptive-group", kind="point_plan")
    def adaptive_grid(experiment: sc.ExperimentContext) -> None:
        experiment.grid(sc.axis(x, (0, 1)))

    invocation = adaptive_grid.build().with_point_grouping(
        "adaptive-comparison",
        varying=(x,),
    )

    with pytest.raises(CheckFailed) as error:
        compile_invocation(invocation.adaptive(_Optimizer(), max_points=8, axes=(x,)))

    assert [problem.code for problem in error.value.problems] == [
        "adaptive_point_grouping_unsupported"
    ]


def test_compile_expands_point_repeat_within_each_point_cloud_row() -> None:
    x = sc.coordinate("x", _INT)

    @sc.experiment(id="test.repeat-points", kind="point_plan")
    def repeated(experiment: sc.ExperimentContext) -> None:
        experiment.points(
            ({x: 1}, {x: 2}),
            repeat=2,
            repeat_mode="point",
        )

    compiled = compile_invocation(repeated.build())
    request_plan = compiled.request.point_plan
    program = compiled.program.program

    assert isinstance(request_plan.domain, PointCloudDomainRecord)
    assert request_plan.domain.columns == ["x"]
    assert request_plan.domain.rows == [{"x": 1}, {"x": 2}]
    assert program.point_domain_layout == "point_cloud"
    assert [axis.id for axis in program.point_domain] == ["x", "repeat"]
    assert _axis_values(program, "x") == (1, 1, 2, 2)
    assert _axis_values(program, "repeat") == (0, 1, 0, 1)


def test_synthetic_repeat_satisfies_an_authored_point_dependency() -> None:
    repeat = sc.coordinate("repeat", _INT)

    @sc.experiment(id="test.repeat-dependency", kind="point_plan")
    def repeated(experiment: sc.ExperimentContext) -> None:
        experiment.grid(repeat=2)
        experiment.alias(repeat, record_id="observed_repeat")

    compiled = compile_invocation(repeated.build())
    request_domain = compiled.request.point_plan.domain
    program = compiled.program.program

    assert isinstance(request_domain, GridDomainRecord)
    assert request_domain.axes == []
    assert [axis.id for axis in program.point_domain] == ["repeat"]
    assert tuple(dependency.id for dependency in program.point_dependencies) == (
        "repeat",
    )
