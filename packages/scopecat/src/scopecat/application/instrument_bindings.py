"""Conservative config-free inspection of ordinary instrument author programs."""

from collections.abc import Mapping

from pydantic import JsonValue

from scopecat.authoring.experiments import Experiment
from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.kernel.errors import CheckFailed
from scopecat.records.author_revision import AuthorRevisionRef
from scopecat.records.instrument_bindings import (
    BindingDiagnostic,
    BindingRequirements,
    InstrumentRequirement,
)


def inspect_instrument_requirements(
    declaration: Experiment[..., object],
    *,
    inputs: Mapping[str, object],
    serialized_inputs: Mapping[str, JsonValue],
    workspace_id: str,
    code_revision: AuthorRevisionRef | None,
    version: str,
) -> BindingRequirements:
    diagnostics: list[BindingDiagnostic] = []
    requirements: list[InstrumentRequirement] = []
    try:
        definition = declaration.bind(**inputs).definition
        if definition.body.child_instances or definition.body.domain_executions:
            raise ValueError(
                "Nested modules or domain execution need an explicitly configured "
                "setup; their complete requirements cannot be inferred here"
            )
        for port in definition.interface.resources:
            selector = port.selector
            if (
                port.scope
                or selector.entity_inputs
                or selector.role.kind == "any"
                or len(selector.interfaces) != 1
            ):
                raise ValueError(
                    "Only unscoped, entity-free instrument ports with one interface "
                    "and default/exact role are supported; provide the intended "
                    "topology and setup"
                )
            requirement = InstrumentRequirement(
                port_id=port.id,
                interface_id=selector.interfaces[0],
                role_id=selector.role.role_id,
            )
            if any(
                (r.interface_id, r.role_id)
                == (requirement.interface_id, requirement.role_id)
                for r in requirements
            ):
                raise ValueError(
                    "Multiple ports have the same interface and role; declare distinct "
                    "intended roles or provide an explicit setup"
                )
            requirements.append(requirement)
        if not requirements:
            raise ValueError(
                "No ordinary instrument requirements were found; use an explicit "
                "software or domain setup"
            )
    except (CheckFailed, ValueError, TypeError, KeyError, AttributeError) as error:
        diagnostics.append(
            BindingDiagnostic(code="requirements_unavailable", message=str(error))
        )
    return BindingRequirements(
        workspace_id=workspace_id,
        code_revision=code_revision,
        experiment_id=declaration.id,
        version=version,
        inputs_hash=sha256_json_hash(dict(serialized_inputs)),
        supported=not diagnostics,
        requirements=tuple(requirements) if not diagnostics else (),
        diagnostics=tuple(diagnostics),
    )
