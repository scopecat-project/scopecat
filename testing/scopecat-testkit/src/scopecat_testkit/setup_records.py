"""Retained evidence fixtures for storage tests, without a running device backend.

Application tests must register devices through DeviceService or import_recipe.
These records model an already captured resolution and never grant device access.
"""

from scopecat.kernel.content_identity import sha256_json_hash
from scopecat.records.device import DeviceRevisionRef
from scopecat.records.setup import (
    ExecutableSetupSnapshot,
    SetupDefinition,
    SetupDeviceResolution,
    SetupInstrumentBinding,
    SetupRevision,
    resolved_setup_hash,
)


def setup_definition(setup: ExecutableSetupSnapshot) -> SetupDefinition:
    return SetupDefinition(
        topology=setup.topology,
        routing=setup.routing,
        domain_target=setup.domain_target,
        scenario=setup.scenario,
        instruments=tuple(
            SetupInstrumentBinding(
                id=spec.id,
                device_id=spec.exclusivity_key,
                default_state=tuple(spec.default_state),
                run_start=spec.run_start,
                success_action=spec.success_action,
                failure_action=spec.failure_action,
            )
            for spec in setup.instrument_registry.instruments
        ),
    )


def retained_setup_revision(
    *, id: str, setup: ExecutableSetupSnapshot, actor: str, note: str = ""
) -> SetupRevision:
    resolution = SetupDeviceResolution(
        definition_id=id,
        definition_hash=setup_definition(setup).content_hash,
        devices=tuple(
            DeviceRevisionRef(
                device_id=spec.exclusivity_key,
                revision_id=f"fixture:{spec.exclusivity_key}",
                content_hash=sha256_json_hash(spec.model_dump(mode="json")),
            )
            for spec in setup.instrument_registry.instruments
        ),
    )
    return SetupRevision(
        id=id,
        setup=setup,
        resolution=resolution,
        content_hash=resolved_setup_hash(setup, resolution),
        actor=actor,
        note=note,
    )
