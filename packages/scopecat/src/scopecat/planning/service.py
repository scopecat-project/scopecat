"""Transient experiment planning workflows."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from time import perf_counter

from scopecat.compiler.bind import bind_program
from scopecat.compiler.frontend.resolution import (
    CompiledInvocation,
    compile_invocation,
)
from scopecat.config.environment import build_config_environment
from scopecat.execution.program import RunProgram
from scopecat.planning.compilation import compile_run_program
from scopecat.planning.system import ExperimentSystem
from scopecat.program.control_contract import ControlValidationContext
from scopecat.program.definitions import ExperimentInvocation
from scopecat.records.config import ConfigProfileSnapshot
from scopecat.records.config_context import ContextRunConfigSource
from scopecat.records.costs import RunCompilationCost
from scopecat.records.run import RunConfigSource
from scopecat.records.run_request import RunRequest
from scopecat.records.sample import SampleSelector


@dataclass(frozen=True, slots=True)
class PlannedRun:
    """Transient program and the accepted facts used to plan it."""

    config: ConfigProfileSnapshot
    request: RunRequest
    program: RunProgram
    config_source: RunConfigSource | None = None
    system: ExperimentSystem | None = field(default=None, repr=False, compare=False)


def _plan_compiled_run(
    *,
    config: ConfigProfileSnapshot,
    experiment: CompiledInvocation,
    system: ExperimentSystem | None,
    config_source: RunConfigSource | None,
) -> PlannedRun:
    environment = build_config_environment(
        config, allow_missing=isinstance(config_source, ContextRunConfigSource)
    )
    bound = bind_program(experiment.program, environment)
    program = compile_run_program(
        system,
        bound=bound,
        adaptive_domain_plan=experiment.adaptive_domain_plan,
    )
    return PlannedRun(
        config=config,
        request=experiment.request,
        program=program,
        config_source=config_source,
        system=system,
    )


def plan_experiment_invocation(
    experiment: ExperimentInvocation,
    *,
    config: ConfigProfileSnapshot,
    system: ExperimentSystem,
    config_source: RunConfigSource | None = None,
    display_name: str | None = None,
    tags: tuple[str, ...] = (),
    description: str | None = None,
    metadata: Mapping[str, object] | None = None,
    operator: str | None = None,
    samples: tuple[SampleSelector, ...] = (),
) -> PlannedRun:
    """Plan one authored invocation against a snapshot without project I/O."""

    if experiment.definition.controls is not None:
        experiment.definition.controls.validate(
            ControlValidationContext(experiment, config)
        )
    started = perf_counter()
    planned = _plan_compiled_run(
        config=config,
        experiment=compile_invocation(
            experiment,
            display_name=display_name,
            tags=tags,
            description=description,
            metadata=metadata,
            operator=operator,
            samples=samples,
        ),
        system=system,
        config_source=config_source,
    )

    return replace(
        planned,
        program=replace(
            planned.program,
            compilation_cost=RunCompilationCost(seconds=perf_counter() - started),
        ),
    )
