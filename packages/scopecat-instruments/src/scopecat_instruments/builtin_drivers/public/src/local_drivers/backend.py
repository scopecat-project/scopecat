"""Explicit built-in driver selection; construction never connects a device."""

from pathlib import Path

from scopecat.sdk.instruments import InstrumentBackend

from scopecat_instruments.package_manifest import PACKAGE_MANIFEST
from scopecat_instruments.provider import ConfiguredInstrumentProvider


def create_backend(project_root: Path) -> InstrumentBackend:
    del project_root
    provider = ConfiguredInstrumentProvider(
        registrations=tuple(
            driver
            for driver in PACKAGE_MANIFEST.drivers
            if driver.connection_kind != "virtual"
        )
    )
    return InstrumentBackend(provider=provider, driver_catalog=provider.driver_catalog)
