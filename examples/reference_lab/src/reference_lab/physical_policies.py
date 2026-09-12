"""Lab-owned physical preparation policies shared by authoring and targets."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import scopecat as sc
from scopecat.kernel.entity import EntityRef

from reference_lab.bench_interfaces import (
    ANALOG_WAVEFORM_OUTPUT,
    ANALOG_WAVEFORM_OUTPUT_OFFSET,
)
from reference_lab.parameters import (
    DRIVE_AWG_OFFSET_GUARD_SLOT_ID,
    AwgOutputBaseline,
    IqChainParameters,
)

IQ_OFFSET_COUPLING_POLICY_ID = "reference_lab.iq-offset.coupling-groups.v2"


@dataclass(frozen=True, slots=True, order=True)
class AwgChannelId:
    """One routed physical DAC output used by target and lab policies."""

    value: str
    instrument_id: str
    component_path: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class IqOffsetOutputSlot:
    """One lab policy slot resolved through a role-bearing physical route."""

    id: str
    role_id: str
    baseline_key: str


DRIVE_AWG_OFFSET_GUARD = IqOffsetOutputSlot(
    id=DRIVE_AWG_OFFSET_GUARD_SLOT_ID,
    role_id="iq-offset-guard",
    baseline_key=DRIVE_AWG_OFFSET_GUARD_SLOT_ID,
)
IQ_OFFSET_OUTPUT_SLOTS = (DRIVE_AWG_OFFSET_GUARD,)


@dataclass(frozen=True, slots=True, order=True)
class OutputOffsetRequirement:
    """Reviewed host-provided offset for one physical output."""

    channel_id: AwgChannelId
    offset_v: float


@dataclass(frozen=True, slots=True)
class OutputOffsetCouplingGroup:
    """Physical outputs whose reviewed offsets are prepared as one closure.

    Activation channels select the group; required offsets may additionally
    include idle guards. Group membership is declared by lab policy rather than
    inferred from instrument identity, so a closure may cover one bank, a whole
    AWG, or a larger physical assembly.
    """

    id: str
    activation_channels: tuple[AwgChannelId, ...]
    output_offsets: tuple[OutputOffsetRequirement, ...]


@dataclass(frozen=True, slots=True)
class IqOffsetCouplingGroupDefinition:
    """One semantic activation and required-state closure before routing."""

    id: str
    activation_chain_ids: tuple[str, ...]
    required_chain_ids: tuple[str, ...]
    required_output_slot_ids: tuple[str, ...] = ()
    activation_chain_prefixes: tuple[str, ...] = ()
    required_chain_prefixes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IqOffsetPolicyDefinition:
    """Lab-owned semantic policy shared by host preparation and target lowering."""

    id: str
    coupling_groups: tuple[IqOffsetCouplingGroupDefinition, ...]


REFERENCE_IQ_OFFSET_POLICY = IqOffsetPolicyDefinition(
    id=IQ_OFFSET_COUPLING_POLICY_ID,
    coupling_groups=(
        IqOffsetCouplingGroupDefinition(
            id="drive-awg.outputs",
            activation_chain_ids=("drive-q0", "drive-q1", "drive-q2", "drive-q3"),
            required_chain_ids=("drive-q0", "drive-q1", "drive-q2", "drive-q3"),
            required_output_slot_ids=(DRIVE_AWG_OFFSET_GUARD_SLOT_ID,),
        ),
        IqOffsetCouplingGroupDefinition(
            id="readout-awg.outputs",
            activation_chain_ids=("readout",),
            required_chain_ids=("readout",),
        ),
    ),
)
SCALABLE_IQ_OFFSET_POLICY = IqOffsetPolicyDefinition(
    id="reference_lab.iq-offset.drive-bank.v1",
    coupling_groups=(
        IqOffsetCouplingGroupDefinition(
            id="drive-awg.outputs",
            activation_chain_ids=(),
            required_chain_ids=(),
            required_output_slot_ids=(DRIVE_AWG_OFFSET_GUARD_SLOT_ID,),
            activation_chain_prefixes=("drive-",),
            required_chain_prefixes=("drive-",),
        ),
        IqOffsetCouplingGroupDefinition(
            id="readout-awg.outputs",
            activation_chain_ids=("readout",),
            required_chain_ids=("readout",),
        ),
    ),
)
IQ_OFFSET_POLICIES = (REFERENCE_IQ_OFFSET_POLICY, SCALABLE_IQ_OFFSET_POLICY)


@dataclass(frozen=True, slots=True)
class IqOffsetCouplingPolicy:
    """Lab-owned physical offset groups available to target compilation."""

    id: str
    coupling_groups: tuple[OutputOffsetCouplingGroup, ...]


def grouped_iq_offset_policy(
    *,
    policy: IqOffsetPolicyDefinition,
    chain_outputs: Mapping[str, Sequence[OutputOffsetRequirement]],
    output_slots: Mapping[str, OutputOffsetRequirement],
) -> IqOffsetCouplingPolicy:
    """Resolve one semantic IQ policy into concrete physical offsets."""

    configured_chain_ids = set(chain_outputs)
    groups = policy.coupling_groups
    group_ids = tuple(group.id for group in groups)
    if len(group_ids) != len(set(group_ids)):
        raise ValueError("IQ offset coupling group ids must be unique")
    activation_chain_groups: dict[str, str] = {}
    required_chain_ids: set[str] = set()
    resolved_groups: list[OutputOffsetCouplingGroup] = []
    channel_groups: dict[AwgChannelId, str] = {}
    for definition in sorted(groups, key=lambda group: group.id):
        activation_outputs: list[OutputOffsetRequirement] = []
        activation_chain_ids = _matching_chain_ids(
            definition.activation_chain_ids,
            prefixes=definition.activation_chain_prefixes,
            configured_chain_ids=configured_chain_ids,
        )
        for chain_id in activation_chain_ids:
            previous_group = activation_chain_groups.get(chain_id)
            if previous_group is not None:
                raise ValueError(
                    f"IQ chain {chain_id!r} activates coupling groups "
                    f"{previous_group!r} and {definition.id!r}"
                )
            activation_chain_groups[chain_id] = definition.id
            activation_outputs.extend(chain_outputs.get(chain_id, ()))
        selected_outputs: list[OutputOffsetRequirement] = []
        selected_chain_ids = _matching_chain_ids(
            definition.required_chain_ids,
            prefixes=definition.required_chain_prefixes,
            configured_chain_ids=configured_chain_ids,
        )
        for chain_id in selected_chain_ids:
            required_chain_ids.add(chain_id)
            selected_outputs.extend(chain_outputs.get(chain_id, ()))
        for slot_id in definition.required_output_slot_ids:
            try:
                selected_outputs.append(output_slots[slot_id])
            except KeyError as error:
                raise ValueError(
                    f"IQ offset policy {policy.id!r} references unresolved output "
                    f"slot {slot_id!r}"
                ) from error
        offsets = _unique_output_offsets(selected_outputs)
        for requirement in offsets:
            previous_group = channel_groups.get(requirement.channel_id)
            if previous_group is not None:
                raise ValueError(
                    f"AWG channel {requirement.channel_id.value!r} belongs to "
                    f"coupling groups {previous_group!r} and {definition.id!r}"
                )
            channel_groups[requirement.channel_id] = definition.id
        resolved_groups.append(
            OutputOffsetCouplingGroup(
                id=definition.id,
                activation_channels=tuple(
                    sorted(
                        {requirement.channel_id for requirement in activation_outputs}
                    )
                ),
                output_offsets=offsets,
            )
        )

    declared_activation_ids = set(activation_chain_groups)
    if missing := sorted(configured_chain_ids - declared_activation_ids):
        raise ValueError(
            "IQ offset coupling policy has no activation group for chains: "
            + ", ".join(missing)
        )
    referenced_chain_ids = declared_activation_ids | required_chain_ids
    if unknown := sorted(referenced_chain_ids - configured_chain_ids):
        raise ValueError(
            "IQ offset coupling policy references unknown chains: " + ", ".join(unknown)
        )
    return IqOffsetCouplingPolicy(
        id=policy.id,
        coupling_groups=tuple(resolved_groups),
    )


def _matching_chain_ids(
    explicit: Sequence[str],
    *,
    prefixes: Sequence[str],
    configured_chain_ids: set[str],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                *explicit,
                *(
                    chain_id
                    for chain_id in sorted(configured_chain_ids)
                    if any(chain_id.startswith(prefix) for prefix in prefixes)
                ),
            )
        )
    )


def iq_offset_policy_definition(policy_id: str) -> IqOffsetPolicyDefinition:
    """Select one lab-owned IQ policy by its configuration id."""

    for policy in IQ_OFFSET_POLICIES:
        if policy.id == policy_id:
            return policy
    raise ValueError(f"unsupported IQ offset policy {policy_id!r}")


def iq_offset_output_slots(
    policy: IqOffsetPolicyDefinition,
) -> tuple[IqOffsetOutputSlot, ...]:
    """Return the semantic output slots referenced by one policy."""

    slots_by_id = {slot.id: slot for slot in IQ_OFFSET_OUTPUT_SLOTS}
    slot_ids = tuple(
        dict.fromkeys(
            slot_id
            for group in policy.coupling_groups
            for slot_id in group.required_output_slot_ids
        )
    )
    if unknown := sorted(set(slot_ids) - set(slots_by_id)):
        raise ValueError(
            f"IQ offset policy {policy.id!r} references unknown output slots: "
            + ", ".join(unknown)
        )
    return tuple(slots_by_id[slot_id] for slot_id in slot_ids)


def ensure_grouped_iq_offsets(
    context: sc.ExperimentContext | sc.ModuleContext,
    *,
    qubits: sc.EachEntity,
    drive_iq_chains: Sequence[tuple[EntityRef, str]],
    policy: IqOffsetPolicyDefinition = REFERENCE_IQ_OFFSET_POLICY,
) -> None:
    """Apply the logical host side of the reviewed IQ-offset policy."""

    drive_i = sc.capability_resource(
        context,
        "drive-i-offset",
        requires=(ANALOG_WAVEFORM_OUTPUT,),
        for_=qubits,
        role="drive-i",
    )
    drive_q = sc.capability_resource(
        context,
        "drive-q-offset",
        requires=(ANALOG_WAVEFORM_OUTPUT,),
        for_=qubits,
        role="drive-q",
    )
    readout_i = sc.capability_resource(
        context,
        "readout-i-offset",
        requires=(ANALOG_WAVEFORM_OUTPUT,),
        for_=qubits,
        role="readout-i",
    )
    readout_q = sc.capability_resource(
        context,
        "readout-q-offset",
        requires=(ANALOG_WAVEFORM_OUTPUT,),
        for_=qubits,
        role="readout-q",
    )
    output_slots = tuple(
        (
            slot,
            sc.capability_resource(
                context,
                f"{slot.id}-offset",
                requires=(ANALOG_WAVEFORM_OUTPUT,),
                role=slot.role_id,
            ),
        )
        for slot in iq_offset_output_slots(policy)
    )
    sc.ensure_state_targets(
        context,
        (
            *(
                drive_i[entity].state_target(
                    {
                        ANALOG_WAVEFORM_OUTPUT_OFFSET: sc.parameter_ref(
                            IqChainParameters.mixer_i_offset, row
                        )
                    }
                )
                for entity, row in drive_iq_chains
            ),
            *(
                drive_q[entity].state_target(
                    {
                        ANALOG_WAVEFORM_OUTPUT_OFFSET: sc.parameter_ref(
                            IqChainParameters.mixer_q_offset, row
                        )
                    }
                )
                for entity, row in drive_iq_chains
            ),
            *(
                readout_i[entity].state_target(
                    {
                        ANALOG_WAVEFORM_OUTPUT_OFFSET: sc.parameter_ref(
                            IqChainParameters.mixer_i_offset, "readout"
                        )
                    }
                )
                for entity in qubits
            ),
            *(
                readout_q[entity].state_target(
                    {
                        ANALOG_WAVEFORM_OUTPUT_OFFSET: sc.parameter_ref(
                            IqChainParameters.mixer_q_offset, "readout"
                        )
                    }
                )
                for entity in qubits
            ),
            *(
                resource.state_target(
                    {
                        ANALOG_WAVEFORM_OUTPUT_OFFSET: sc.parameter_ref(
                            AwgOutputBaseline.offset, slot.baseline_key
                        )
                    }
                )
                for slot, resource in output_slots
            ),
        ),
    )


def _unique_output_offsets(
    requirements: Sequence[OutputOffsetRequirement],
) -> tuple[OutputOffsetRequirement, ...]:
    offsets: dict[AwgChannelId, float] = {}
    for requirement in requirements:
        channel_id = requirement.channel_id
        existing = offsets.get(channel_id)
        if existing is not None and existing != requirement.offset_v:
            raise ValueError(
                f"shared AWG channel {channel_id.value!r} has conflicting offsets"
            )
        offsets[channel_id] = requirement.offset_v
    return tuple(
        OutputOffsetRequirement(channel_id=channel_id, offset_v=offset_v)
        for channel_id, offset_v in sorted(offsets.items())
    )


__all__ = [
    "DRIVE_AWG_OFFSET_GUARD",
    "IQ_OFFSET_COUPLING_POLICY_ID",
    "IQ_OFFSET_OUTPUT_SLOTS",
    "IQ_OFFSET_POLICIES",
    "REFERENCE_IQ_OFFSET_POLICY",
    "SCALABLE_IQ_OFFSET_POLICY",
    "AwgChannelId",
    "IqOffsetCouplingGroupDefinition",
    "IqOffsetCouplingPolicy",
    "IqOffsetOutputSlot",
    "IqOffsetPolicyDefinition",
    "OutputOffsetCouplingGroup",
    "OutputOffsetRequirement",
    "ensure_grouped_iq_offsets",
    "grouped_iq_offset_policy",
    "iq_offset_output_slots",
    "iq_offset_policy_definition",
]
