"""Source-controlled files for a runnable local lab project."""

from __future__ import annotations

from pathlib import Path

_PROJECT_FILES = {
    "scopecat.toml": """\
[lab]
bootstrap = "scopecat_lab.application:create_bootstrap"
application = "scopecat_lab.application:create_application"
instrument_backend = "scopecat_lab.backend:create_backend"
""",
    "src/scopecat_lab/__init__.py": '''\
"""User-owned composition for this Scopecat project."""
''',
    "src/scopecat_lab/configuration.py": '''\
"""Editable Python source for the project's initial configuration."""

from __future__ import annotations

import scopecat as sc
from scopecat.kernel.entity import EntityRef
from scopecat.records.config import (
    ConfigProfileSnapshot,
    InstrumentRegistry,
    InstrumentSpec,
    ResourceRoute,
    RoutingEndpoint,
    RoutingGraph,
    SystemSpec,
    Topology,
    VirtualInstrumentConnection,
    snapshot_config_profile,
)
from scopecat.records.parameter import (
    ParameterCatalog,
    ParameterDefinition,
    ParameterSnapshot,
    ScalarParameterValue,
)

DEFAULT_REPETITIONS = 128


def bootstrap_config() -> ConfigProfileSnapshot:
    """Build the default config used only while the daemon registry is empty."""

    return snapshot_config_profile(
        profile_id="default",
        system=SystemSpec(
            id="default-system",
            primary_entity_id="subject",
            topology=Topology(
                entities=[EntityRef(id="subject", kind="logical_subject")],
            ),
            instrument_registry=InstrumentRegistry(
                instruments=[
                    InstrumentSpec(
                        id="thermometer",
                        exclusivity_key="thermometer",
                        driver_id="scopecat.virtual.temperature_monitor",
                        connection=VirtualInstrumentConnection(),
                        run_start="preserve",
                        success_action="release",
                        failure_action="abort_and_release",
                    ),
                ]
            ),
            routing=RoutingGraph(
                routes=[
                    ResourceRoute(
                        id="thermometer",
                        instrument_id="thermometer",
                        entity_ids=["subject"],
                        endpoints=[
                            RoutingEndpoint(
                                interface_id="scopecat.temperature_readout/v1",
                                entity_id="subject",
                            )
                        ],
                    ),
                ]
            ),
            domain_target=None,
            parameter_catalog=ParameterCatalog(
                id="parameters",
                definitions=(
                    ParameterDefinition(
                        id="repetitions",
                        value_type=sc.ScalarType(sc.IntType(minimum=1)),
                        description="Default number of repeated acquisitions.",
                    ),
                ),
            ),
        ),
        parameter_snapshot=ParameterSnapshot(
            id="default-values",
            values=(
                ScalarParameterValue(
                    id="repetitions",
                    value=DEFAULT_REPETITIONS,
                ),
            ),
        ),
    )


__all__ = ["bootstrap_config"]
''',
    "src/scopecat_lab/application.py": '''\
"""Daemon bootstrap and project-worker composition for this project."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from scopecat.application import LabBootstrap

from .configuration import bootstrap_config

if TYPE_CHECKING:
    from scopecat.application import LabApplication


def create_bootstrap(_project_root: Path) -> LabBootstrap:
    """Expose only config construction to the daemon process."""

    return LabBootstrap(bootstrap_config=bootstrap_config)


def create_application(_project_root: Path) -> LabApplication:
    """Compose notebook and project-worker execution capabilities."""

    from scopecat.application import LabApplication

    return LabApplication()


__all__ = ["create_application", "create_bootstrap"]
''',
    "src/scopecat_lab/backend.py": '''\
"""Worker-only instrument backend composition for this project."""

from __future__ import annotations

from pathlib import Path

from scopecat.sdk.instruments import InstrumentBackend
from scopecat_instruments import ConfiguredInstrumentProvider


def create_backend(_project_root: Path) -> InstrumentBackend:
    provider = ConfiguredInstrumentProvider(seed=0)
    return InstrumentBackend(
        provider=provider,
        driver_catalog=provider.driver_catalog,
    )


__all__ = ["create_backend"]
''',
    "notebooks/01_first_run.py": '''\
"""Retain one virtual thermometer sample through the project daemon."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlencode

import scopecat as sc
from scopecat.daemon.endpoint import resolve_daemon_endpoint
from scopecat.kernel.entity import EntityRef
from scopecat_instruments import TemperatureSampleProducts, temperature_readout

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@sc.experiment
def first_run(experiment: sc.ExperimentContext) -> TemperatureSampleProducts:
    """Read the virtual thermometer without applying instrument state."""

    thermometer = temperature_readout(
        experiment, for_=sc.one(EntityRef(id="subject", kind="logical_subject"))
    )
    return thermometer.sample()


# %%
project = sc.open_project(PROJECT_ROOT)
with project.connect() as lab:
    run = lab.run(first_run(), name="First run")
    summary = {"run_id": run.id, "status": run.status}

print(summary)
print(resolve_daemon_endpoint(PROJECT_ROOT) + "/?" + urlencode({"run": run.id}))
''',
}


def scaffold_paths(root: Path) -> tuple[Path, ...]:
    """Return every file owned by project initialization."""

    return tuple(root / relative_path for relative_path in _PROJECT_FILES)


def write_project_scaffold(root: Path) -> None:
    """Write a preflighted scaffold without replacing existing files."""

    for relative_path, content in _PROJECT_FILES.items():
        destination = root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")


__all__ = ["scaffold_paths", "write_project_scaffold"]
