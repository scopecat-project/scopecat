"""Stable, causal float64 SOS computation, independent of measurement/fitting."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Annotated, cast

import numpy as np
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, Field, model_validator
from scipy.linalg import solve_discrete_lyapunov  # pyright: ignore[reportUnknownVariableType]
from scipy.signal import sosfilt  # pyright: ignore[reportUnknownVariableType]
from scopecat.kernel.content_identity import sha256_json_hash

type Finite = Annotated[float, Field(allow_inf_nan=False)]
type SosSection = tuple[Finite, Finite, Finite, Finite, Finite, Finite]


@dataclass(frozen=True, slots=True)
class SosState:
    """DF-II transposed delays for one continuous record and exact filter."""

    filter_fingerprint: str
    delays: tuple[tuple[float, float], ...]


class SosIir(BaseModel):
    """Ordered sections: (b0,b1,b2,a0,a1,a2), a0=1, negative feedback.

    H(z) = product((b0+b1/z+b2/z²)/(1+a1/z+a2/z²)). No resampling,
    normalization, gain adjustment or implicit delay cancellation is performed.
    Strictly stable poles are required by this bounded zero-baseline capability;
    future high-pass/integrator inverse models need their own boundary contract.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    sample_rate_hz: Annotated[int, Field(strict=True, gt=0)]
    sections: Annotated[tuple[SosSection, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def validate_sections(self) -> SosIir:
        for _, _, _, a0, a1, a2 in self.sections:
            if a0 != 1:
                raise ValueError("SOS a0 must be normalized to exactly 1")
            # Real second-order Jury criterion, including first-order a2=0.
            if not (abs(a2) < 1 and 1 + a1 + a2 > 0 and 1 - a1 + a2 > 0):
                raise ValueError("SOS poles must be strictly inside the unit circle")
        return self

    @property
    def fingerprint(self) -> str:
        return sha256_json_hash(self.model_dump(mode="json"))

    def filter_chunk(
        self, samples: NDArray[np.float64], *, state: SosState | None = None
    ) -> tuple[NDArray[np.float64], SosState]:
        """Consume one real 1D chunk; None starts a new independent shot at rest.

        Pass the returned state to the next chunk of the *same* continuous
        record. Empty chunks preserve state. No tail is appended here.
        """
        x = np.asarray(samples)
        if x.ndim != 1 or x.dtype.kind not in "fiu" or not np.all(np.isfinite(x)):
            raise ValueError("IIR samples must be finite real one-dimensional values")
        delays = np.zeros((len(self.sections), 2), dtype=np.float64)
        if state is not None:
            delays = np.asarray(state.delays, dtype=np.float64)
            if (
                state.filter_fingerprint != self.fingerprint
                or delays.shape != (len(self.sections), 2)
                or not np.all(np.isfinite(delays))
            ):
                raise ValueError("IIR state does not match this filter")
        if len(x):
            with np.errstate(over="ignore", invalid="ignore"):
                y, delays = cast(
                    "tuple[NDArray[np.float64], NDArray[np.float64]]",
                    sosfilt(
                        np.asarray(self.sections, dtype=np.float64),
                        np.asarray(x, dtype=np.float64),
                        zi=delays,
                    ),
                )
        else:
            y = np.empty(0, dtype=np.float64)
        if not np.all(np.isfinite(y)) or not np.all(np.isfinite(delays)):
            raise ValueError("IIR computation produced nonfinite samples/state")
        return y, SosState(
            self.fingerprint, tuple((float(d[0]), float(d[1])) for d in delays)
        )

    def remaining_output_bound(self, state: SosState) -> float:
        """Conservative bound on *all* future zero-input samples in input units.

        A discrete Lyapunov certificate bounds infinite output energy, hence
        every future sample. A small endpoint alone cannot certify settling.
        Ill-conditioned certificates fail closed instead of guessing a tail.
        """
        self.filter_chunk(np.empty(0, dtype=np.float64), state=state)
        z = np.asarray(state.delays, dtype=np.float64).reshape(-1)
        if not np.any(z):
            return 0.0
        size = len(z)
        transition = np.empty((size, size), dtype=np.float64)
        output = np.empty(size, dtype=np.float64)
        for column in range(size):
            basis = np.zeros(size, dtype=np.float64)
            basis[column] = 1
            y, final = cast(
                "tuple[NDArray[np.float64], NDArray[np.float64]]",
                sosfilt(
                    np.asarray(self.sections, dtype=np.float64),
                    np.zeros(1, dtype=np.float64),
                    zi=basis.reshape(-1, 2),
                ),
            )
            output[column] = y[0]
            transition[:, column] = np.asarray(final).reshape(-1)
        gram = np.outer(output, output)
        try:
            certificate = np.asarray(
                cast(
                    "NDArray[np.float64]",
                    solve_discrete_lyapunov(transition.T, gram + np.eye(size)),
                ),
                dtype=np.float64,
            )
            certificate = (certificate + certificate.T) / 2
            residual = certificate - transition.T @ certificate @ transition - gram
            if (
                not np.all(np.isfinite(certificate))
                or not np.all(np.isfinite(residual))
                or np.min(np.linalg.eigvalsh(certificate)) <= 0
                or np.min(np.linalg.eigvalsh(residual)) < 0.5
            ):
                raise ValueError("IIR tail certificate is numerically ill-conditioned")
            scale = float(np.max(np.abs(z)))
            normalized = z / scale
            energy = float(normalized @ certificate @ normalized)
        except np.linalg.LinAlgError as exc:
            raise ValueError("IIR tail certificate could not be computed") from exc
        if not math.isfinite(energy) or energy < 0:
            raise ValueError("IIR tail certificate is nonfinite")
        return math.sqrt(energy) * scale


def inverse_exponential_sos(
    *, amplitude: float, tau_seconds: float, sample_rate_hz: int
) -> SosIir:
    """Invert the measured discrete step s[n]=1+A exp(-n/(fs*tau)), n>=0.

    The pre-step baseline is 0 and final plateau 1; A>0 decays from above,
    A<0 recovers from below. This describes ONE exponential, not a sum or an
    assumed cascade. Externally computed general/multi-exponential SOS can be
    supplied directly. The result is not obtained by negating A.
    """
    if (
        not math.isfinite(amplitude)
        or not math.isfinite(tau_seconds)
        or tau_seconds <= 0
        or isinstance(sample_rate_hz, bool)
        or not isinstance(sample_rate_hz, int)
        or sample_rate_hz <= 0
    ):
        raise ValueError(
            "exponential parameters require finite A, tau>0 and integer fs>0"
        )
    decay = math.exp(-1 / (sample_rate_hz * tau_seconds))
    if not 0 < decay < 1 or amplitude <= -1:
        raise ValueError("exponential response has no supported stable causal inverse")
    # H_response=(1+A-(decay+A)z^-1)/(1-decay z^-1).
    # Invert this transfer function, then normalize its denominator.
    scale = 1 + amplitude
    return SosIir(
        sample_rate_hz=sample_rate_hz,
        sections=((1 / scale, -decay / scale, 0, 1, -(decay + amplitude) / scale, 0),),
    )
