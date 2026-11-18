"""Pure declared candidates and explicit binding checks shared by all clients."""

from scopecat.records.config import (
    ResourceRoleSpec,
    ResourceRoute,
    RoutingEndpoint,
    RoutingGraph,
    Topology,
)
from scopecat.records.instrument_bindings import (
    BindingCandidate,
    BindingCheck,
    BindingDiagnostic,
    BindingOptions,
    BindingRequirements,
    InstrumentBindingPlan,
)
from scopecat.records.setup import SetupDefinition, SetupInstrumentBinding

from scopecat_server.services.devices import DeviceService


def _requirements_diagnostic(
    requirements: BindingRequirements,
) -> BindingDiagnostic | None:
    ports = requirements.requirements
    if not requirements.supported or not ports:
        return BindingDiagnostic(
            code="requirements_unavailable",
            message="Inspect supported requirements on the selected author revision",
        )
    if len({p.port_id for p in ports}) != len(ports) or len(
        {(p.interface_id, p.role_id) for p in ports}
    ) != len(ports):
        return BindingDiagnostic(
            code="invalid_requirements",
            message="Requirement IDs and interface/role selectors must be unique",
        )
    return None


def binding_options(
    devices: DeviceService, requirements: BindingRequirements
) -> BindingOptions:
    candidates: list[BindingCandidate] = []
    diagnostics = list(requirements.diagnostics)
    if problem := _requirements_diagnostic(requirements):
        return BindingOptions(
            requirements=requirements, diagnostics=(*diagnostics, problem)
        )
    for view in devices.list():
        if view.device.state != "available":
            continue
        try:
            description = devices.declaration(
                view.device.id, connection_revision_id=view.revision.id
            )
        except (ValueError, RuntimeError) as error:
            diagnostics.append(
                BindingDiagnostic(
                    code="declaration_unavailable",
                    message=str(error),
                    device_id=view.device.id,
                )
            )
            continue
        for requirement in requirements.requirements:
            if not any(
                i.id == requirement.interface_id for i in description.interfaces
            ):
                continue
            mounts = [
                tuple(m.component_path)
                for m in description.interface_mounts
                if m.interface_id == requirement.interface_id
            ]
            # The declaration contract mounts an interface at root when none are given.
            for path in mounts or [()]:
                candidates.append(
                    BindingCandidate(
                        port_id=requirement.port_id,
                        device_id=view.device.id,
                        device_label=view.device.label,
                        connection_revision_id=view.revision.id,
                        interface_id=requirement.interface_id,
                        component_path=path,
                    )
                )
    for requirement in requirements.requirements:
        matches = [c for c in candidates if c.port_id == requirement.port_id]
        if not matches:
            diagnostics.append(
                BindingDiagnostic(
                    code="no_matching_endpoint",
                    message=(
                        "No declared endpoint matches this exact interface; unknown "
                        "declarations are not candidates"
                    ),
                    port_id=requirement.port_id,
                )
            )
        elif len(matches) > 1:
            diagnostics.append(
                BindingDiagnostic(
                    code="selection_required",
                    message="Multiple declared endpoints match; explicitly choose one",
                    port_id=requirement.port_id,
                )
            )
    return BindingOptions(
        requirements=requirements,
        candidates=tuple(candidates),
        diagnostics=tuple(diagnostics),
    )


def check_bindings(devices: DeviceService, plan: InstrumentBindingPlan) -> BindingCheck:
    if problem := _requirements_diagnostic(plan.requirements):
        return BindingCheck(diagnostics=(problem,))
    diagnostics: list[BindingDiagnostic] = []
    bindings: dict[str, SetupInstrumentBinding] = {}
    routes: list[ResourceRoute] = []
    roles: dict[str, ResourceRoleSpec] = {}
    expected = {r.port_id for r in plan.requirements.requirements}
    if len(expected) != len(plan.requirements.requirements) or any(
        c.port_id not in expected for c in plan.choices
    ):
        return BindingCheck(
            diagnostics=(
                BindingDiagnostic(
                    code="invalid_selection",
                    message="Unknown or duplicate requirement identity",
                ),
            )
        )
    for requirement in plan.requirements.requirements:
        choices = [c for c in plan.choices if c.port_id == requirement.port_id]
        if len(choices) != 1:
            diagnostics.append(
                BindingDiagnostic(
                    code="selection_required",
                    message="Select exactly one endpoint for this port",
                    port_id=requirement.port_id,
                )
            )
            continue
        choice = choices[0]
        try:
            view = devices.get(choice.device_id)
            if view.device.state != "available":
                raise ValueError("Device is retired")
            description = devices.declaration(
                choice.device_id, connection_revision_id=choice.connection_revision_id
            )
            interfaces = {i.id for i in description.interfaces}
            mounts = [
                tuple(m.component_path)
                for m in description.interface_mounts
                if m.interface_id == requirement.interface_id
            ]
            if (
                requirement.interface_id not in interfaces
                or choice.component_path not in (mounts or [()])
            ):
                raise ValueError(
                    "Endpoint is not declared for this device/interface revision"
                )
            binding = SetupInstrumentBinding(
                id=choice.device_id,
                device_id=choice.device_id,
                run_start=choice.run_start,
                success_action=choice.success_action,
                failure_action=choice.failure_action,
            )
            binding.resolve(view.revision)  # Existing device safety policy authority.
            if choice.device_id in bindings and bindings[choice.device_id] != binding:
                raise ValueError(
                    "One device must have one explicitly chosen lifecycle policy"
                )
            bindings[choice.device_id] = binding
            if requirement.role_id is not None:
                roles[requirement.role_id] = ResourceRoleSpec(id=requirement.role_id)
            routes.append(
                ResourceRoute(
                    id=requirement.port_id,
                    instrument_id=choice.device_id,
                    role_id=requirement.role_id,
                    endpoints=[
                        RoutingEndpoint(
                            interface_id=requirement.interface_id,
                            component_path=choice.component_path,
                        )
                    ],
                )
            )
        except (ValueError, RuntimeError, KeyError) as error:
            diagnostics.append(
                BindingDiagnostic(
                    code="binding_invalid",
                    message=str(error),
                    port_id=requirement.port_id,
                    device_id=choice.device_id,
                )
            )
    if diagnostics:
        return BindingCheck(diagnostics=tuple(diagnostics))
    try:
        return BindingCheck(
            definition=SetupDefinition(
                topology=Topology(),
                instruments=tuple(bindings.values()),
                routing=RoutingGraph(roles=list(roles.values()), routes=routes),
                domain_target=None,
                scenario=plan.scenario,
            )
        )
    except ValueError as error:
        return BindingCheck(
            diagnostics=(BindingDiagnostic(code="binding_invalid", message=str(error)),)
        )
