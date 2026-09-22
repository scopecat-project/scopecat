"""Factories loaded independently by benchmark client, daemon, and driver worker."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast


def create_bootstrap(project_root: Path):
    """Load the exact generated benchmark config in the daemon process."""

    import scopecat_server.runtime as daemon_runtime  # noqa: TID251
    from scopecat.application import LabBootstrap
    from scopecat.records.config import ConfigProfileSnapshot
    from scopecat.records.parameter_revision import ParameterRevisionContent
    from scopecat.records.setup import ExecutableSetupSnapshot

    from .daemon_telemetry import telemetry_payload_service

    daemon_runtime.CommandPayloadService = telemetry_payload_service(  # pyright: ignore[reportPrivateLocalImportUsage]
        project_root
    )

    config = ConfigProfileSnapshot.model_validate_json(
        (project_root / "benchmark-config.json").read_text(encoding="utf-8")
    )
    return LabBootstrap(
        setup=lambda: ExecutableSetupSnapshot.from_config(config),
        parameter_defaults=lambda: ParameterRevisionContent(
            id=config.id,
            system_id=config.system.id,
            catalog=config.parameter_catalog,
            parameters=config.parameter_snapshot,
        ),
    )


def create_application(project_root: Path):
    """Build the quantum system in the notebook/client process."""

    del project_root
    from reference_lab.lab import reference_lab_system
    from scopecat.application import LabApplication

    return LabApplication(
        build_experiment_system=lambda config, instrument_catalog: reference_lab_system(
            config=config,
            instrument_catalog=instrument_catalog,
        )
    )


def create_backend(project_root: Path):
    """Build telemetry-wrapped drivers in the spawned instrument process."""

    from reference_lab.payloads import reference_lab_payload_codecs
    from reference_lab.provider import ReferenceLabProvider
    from scopecat.sdk.instruments import InstrumentBackend

    from .telemetry import TelemetryProvider

    settings = cast(
        "dict[str, object]",
        json.loads((project_root / "benchmark-run.json").read_text(encoding="utf-8")),
    )
    delegate = ReferenceLabProvider(seed=7)
    provider = TelemetryProvider(
        delegate,
        project_root=project_root,
        live_waveform=cast("bool", settings["live_waveform"]),
        point_delay_s=cast("float", settings["point_delay_s"]),
    )
    return InstrumentBackend(
        provider=provider,
        driver_catalog=delegate.driver_catalog,
        payload_codecs=reference_lab_payload_codecs(),
    )


__all__ = ["create_application", "create_backend", "create_bootstrap"]
