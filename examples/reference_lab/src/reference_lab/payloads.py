"""Typed payload contracts shared by reference-lab planning and workers."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Annotated, ClassVar, Literal, Self, cast

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from scopecat.kernel.numpy_storage import freeze_ndarray
from scopecat.sdk.payloads import PayloadCodecRegistry, PayloadContract
from scopecat.sdk.structured_payloads import (
    FrozenFloat64Vector,
    pydantic_buffer_bundle_codec,
)

from reference_lab.virtual_lab.capture_payload import VIRTUAL_CAPTURE_QUEUE_PAYLOAD

AWG_PROGRAM_SCHEMA_ID = "reference_lab.awg_program.v4"
DIGITIZER_PROGRAM_SCHEMA_ID = "reference_lab.digitizer_program.v1"
TRIGGER_PROGRAM_SCHEMA_ID = "reference_lab.trigger_program.v1"


class _PayloadDocument(BaseModel):
    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        strict=True,
    )


class AwgChannelWaveformDocument(_PayloadDocument):
    component_path: tuple[str, ...] = Field(min_length=1)
    samples: FrozenFloat64Vector


class AwgEntryDocument(_PayloadDocument):
    waveforms: tuple[AwgChannelWaveformDocument, ...]


class MaterializedAwgProgramDocument(_PayloadDocument):
    """Ordinary per-entry DAC buffers ready for an AWG driver."""

    kind: Literal["materialized"]
    max_abs_amplitude: float
    entries: tuple[AwgEntryDocument, ...]


class AwgMixerDocument(_PayloadDocument):
    ii: float
    iq: float
    qi: float
    qq: float


class AwgPhaseTemplateDocument(_PayloadDocument):
    id: str = Field(min_length=1)
    i_component_path: tuple[str, ...] = Field(min_length=1)
    q_component_path: tuple[str, ...] = Field(min_length=1)
    start_sample: int = Field(ge=0)
    logical_i: FrozenFloat64Vector
    logical_q: FrozenFloat64Vector
    mixer: AwgMixerDocument

    @model_validator(mode="after")
    def validate_equal_logical_sample_counts(self) -> Self:
        if len(self.logical_i) != len(self.logical_q):
            raise ValueError("AWG phase-template bases must have equal lengths")
        return self


class AwgPhaseTemplateUseDocument(_PayloadDocument):
    template_id: str = Field(min_length=1)
    phase_radians: float


@dataclass(frozen=True, slots=True)
class _PreparedAwgPhaseComponent:
    component_path: tuple[str, ...]
    phase_zero: NDArray[np.float64] = field(repr=False)
    phase_quadrature: NDArray[np.float64] = field(repr=False)


@dataclass(frozen=True, slots=True)
class _PreparedAwgPhaseTemplate:
    start_sample: int
    components: tuple[_PreparedAwgPhaseComponent, ...]


class PhaseSynthesizedAwgEntryDocument(_PayloadDocument):
    sample_count: int = Field(gt=0)
    template_uses: tuple[AwgPhaseTemplateUseDocument, ...]


class PhaseSynthesizedAwgProgramDocument(_PayloadDocument):
    """Compact phase rows that become ordinary buffers before upload."""

    kind: Literal["phase_synthesized"]
    max_abs_amplitude: float
    templates: tuple[AwgPhaseTemplateDocument, ...]
    entries: tuple[PhaseSynthesizedAwgEntryDocument, ...]

    def materialize(self) -> MaterializedAwgProgramDocument:
        templates = {
            template.id: _prepare_phase_template(template)
            for template in self.templates
        }
        materialized = MaterializedAwgProgramDocument(
            kind="materialized",
            max_abs_amplitude=self.max_abs_amplitude,
            entries=tuple(
                _materialize_phase_entry(entry, templates=templates)
                for entry in self.entries
            ),
        )
        _validate_awg_amplitudes(materialized)
        return materialized


type AwgProgramDocument = Annotated[
    MaterializedAwgProgramDocument | PhaseSynthesizedAwgProgramDocument,
    Field(discriminator="kind"),
]


def materialize_awg_program(
    program: AwgProgramDocument,
) -> MaterializedAwgProgramDocument:
    """Produce validated contiguous buffers at the simple-AWG upload boundary."""

    if isinstance(program, PhaseSynthesizedAwgProgramDocument):
        return program.materialize()
    _validate_awg_amplitudes(program)
    return program


class DigitizerDspWindowDocument(_PayloadDocument):
    component_path: tuple[str, ...] = Field(min_length=1)
    demodulator_slot_id: str = Field(min_length=1)
    start_sample: int = Field(ge=0)
    sample_count: int = Field(gt=0)
    demodulation_frequency_hz: float
    semantics_id: Literal["reference_lab.integrated_iq.ssb_midpoint.v1"]
    normalization: Literal["single_sideband_amplitude"]


class DigitizerProgramEntryDocument(_PayloadDocument):
    sample_count: int = Field(gt=0)
    input_component_paths: tuple[tuple[str, ...], ...]
    windows: tuple[DigitizerDspWindowDocument, ...]


class DigitizerProgramDocument(_PayloadDocument):
    entries: tuple[DigitizerProgramEntryDocument, ...] = Field(min_length=1)


class TriggerProgramEntryDocument(_PayloadDocument):
    awg_instrument_ids: tuple[str, ...]
    digitizer_instrument_ids: tuple[str, ...]


class TriggerProgramDocument(_PayloadDocument):
    program_id: str = Field(min_length=1)
    repetitions: int = Field(gt=0)
    entries: tuple[TriggerProgramEntryDocument, ...] = Field(min_length=1)


AWG_PROGRAM_PAYLOAD = PayloadContract(
    schema_id=AWG_PROGRAM_SCHEMA_ID,
    codec=pydantic_buffer_bundle_codec(
        TypeAdapter[AwgProgramDocument](AwgProgramDocument)
    ),
)
DIGITIZER_PROGRAM_PAYLOAD = PayloadContract(
    schema_id=DIGITIZER_PROGRAM_SCHEMA_ID,
    codec=pydantic_buffer_bundle_codec(DigitizerProgramDocument),
)
TRIGGER_PROGRAM_PAYLOAD = PayloadContract(
    schema_id=TRIGGER_PROGRAM_SCHEMA_ID,
    codec=pydantic_buffer_bundle_codec(TriggerProgramDocument),
)


def reference_lab_payload_codecs() -> PayloadCodecRegistry:
    return PayloadCodecRegistry.from_contracts(
        AWG_PROGRAM_PAYLOAD,
        DIGITIZER_PROGRAM_PAYLOAD,
        TRIGGER_PROGRAM_PAYLOAD,
        VIRTUAL_CAPTURE_QUEUE_PAYLOAD,
    )


def _materialize_phase_entry(
    entry: PhaseSynthesizedAwgEntryDocument,
    *,
    templates: dict[str, _PreparedAwgPhaseTemplate],
) -> AwgEntryDocument:
    buffers: dict[tuple[str, ...], NDArray[np.float64]] = {}
    for use in entry.template_uses:
        template = templates[use.template_id]
        cosine = math.cos(use.phase_radians)
        sine = math.sin(use.phase_radians)
        for component in template.components:
            selected = slice(
                template.start_sample,
                template.start_sample + component.phase_zero.size,
            )
            buffer = buffers.get(component.component_path)
            if buffer is None:
                buffer = np.zeros(entry.sample_count, dtype=np.float64)
                buffers[component.component_path] = buffer
                np.multiply(component.phase_zero, cosine, out=buffer[selected])
            else:
                buffer[selected] += cosine * component.phase_zero
            buffer[selected] += sine * component.phase_quadrature
    return AwgEntryDocument(
        waveforms=tuple(
            AwgChannelWaveformDocument(
                component_path=component_path,
                samples=samples,
            )
            for component_path, samples in sorted(buffers.items())
        )
    )


def _prepare_phase_template(
    template: AwgPhaseTemplateDocument,
) -> _PreparedAwgPhaseTemplate:
    components = tuple(
        _PreparedAwgPhaseComponent(
            component_path=component_path,
            phase_zero=cast(
                "NDArray[np.float64]",
                freeze_ndarray(
                    np.ascontiguousarray(
                        mixer_i * template.logical_i + mixer_q * template.logical_q
                    )
                ),
            ),
            phase_quadrature=cast(
                "NDArray[np.float64]",
                freeze_ndarray(
                    np.ascontiguousarray(
                        -mixer_i * template.logical_q + mixer_q * template.logical_i
                    )
                ),
            ),
        )
        for component_path, mixer_i, mixer_q in (
            (template.i_component_path, template.mixer.ii, template.mixer.iq),
            (template.q_component_path, template.mixer.qi, template.mixer.qq),
        )
    )
    return _PreparedAwgPhaseTemplate(
        start_sample=template.start_sample,
        components=components,
    )


def _validate_awg_amplitudes(program: MaterializedAwgProgramDocument) -> None:
    for entry_index, entry in enumerate(program.entries):
        for waveform in entry.waveforms:
            peak = (
                float(cast("np.float64", np.max(np.abs(waveform.samples))))
                if waveform.samples.size
                else 0.0
            )
            if peak > program.max_abs_amplitude:
                component = "/".join(waveform.component_path)
                raise ValueError(
                    f"AWG entry {entry_index} waveform {component!r} has magnitude "
                    f"{peak!r}; device limit is {program.max_abs_amplitude!r}"
                )


__all__ = [
    "AWG_PROGRAM_PAYLOAD",
    "AWG_PROGRAM_SCHEMA_ID",
    "DIGITIZER_PROGRAM_PAYLOAD",
    "DIGITIZER_PROGRAM_SCHEMA_ID",
    "TRIGGER_PROGRAM_PAYLOAD",
    "TRIGGER_PROGRAM_SCHEMA_ID",
    "AwgChannelWaveformDocument",
    "AwgEntryDocument",
    "AwgMixerDocument",
    "AwgPhaseTemplateDocument",
    "AwgPhaseTemplateUseDocument",
    "AwgProgramDocument",
    "DigitizerDspWindowDocument",
    "DigitizerProgramDocument",
    "DigitizerProgramEntryDocument",
    "MaterializedAwgProgramDocument",
    "PhaseSynthesizedAwgEntryDocument",
    "PhaseSynthesizedAwgProgramDocument",
    "TriggerProgramDocument",
    "TriggerProgramEntryDocument",
    "materialize_awg_program",
    "reference_lab_payload_codecs",
]
