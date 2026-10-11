"""Worker-only instrument backend composition for the reference lab."""

from __future__ import annotations

from pathlib import Path

from scopecat.sdk.instruments import InstrumentBackend


def create_backend(project_root: Path) -> InstrumentBackend:
    """Create the project backend inside its instrument worker."""

    del project_root
    from reference_lab.payloads import reference_lab_payload_codecs
    from reference_lab.provider import ReferenceLabProvider

    provider = ReferenceLabProvider()
    return InstrumentBackend(
        provider=provider,
        driver_catalog=provider.driver_catalog,
        payload_codecs=reference_lab_payload_codecs(),
    )


__all__ = [
    "create_backend",
]
