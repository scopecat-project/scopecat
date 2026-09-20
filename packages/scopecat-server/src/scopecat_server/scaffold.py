"""Source-controlled files for a runnable local lab project."""

from __future__ import annotations

from pathlib import Path

_PROJECT_FILES = {
    "scopecat.toml": """\
[lab]
bootstrap = "scopecat_lab.application:create_bootstrap"
instrument_backend = "scopecat_lab.backend:create_backend"

[lab.capabilities]
author_modules = ["scopecat_lab.authored"]

[authors]
dependencies = ["scopecat-instruments"]
source_roots = ["src"]
refresh_roots = ["src/scopecat_lab/authored"]
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
from scopecat.records.execution_scenario import SoftwareExecutionScenario
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
            scenario=SoftwareExecutionScenario(
                id="starter-software",
                label="Software experiment bench",
                model_id="scopecat.starter.responses",
                model_version="1",
                capabilities=("Virtual temperature readings", "Analytic signal scans"),
                limitations=(
                    "Synthetic responses, not a model of a physical sample.",
                    "No physical instrument connections.",
                ),
            ),
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
"""Initial configuration bootstrap for this project."""

from __future__ import annotations

from pathlib import Path

from scopecat.application import LabBootstrap
from scopecat.records.configuration_template import ConfigurationTemplate

from .configuration import bootstrap_config


def create_bootstrap(_project_root: Path) -> LabBootstrap:
    """Expose only config construction to the daemon process."""

    return LabBootstrap(
        bootstrap_config=bootstrap_config,
        configuration_templates=lambda: (
            ConfigurationTemplate(
                id="starter-software",
                label="Software experiment bench",
                description=(
                    "Virtual temperature readings and analytic signal scans. "
                    "Import fresh parameters without changing existing defaults."
                ),
                config=bootstrap_config(),
            ),
        ),
    )


__all__ = ["create_bootstrap"]
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
    run = lab.run(first_run.build(), name="First run")
    summary = {"run_id": run.id, "status": run.status}

print(summary)
print(resolve_daemon_endpoint(PROJECT_ROOT) + "/?" + urlencode({"run": run.id}))
''',
    "README.md": """\
# Your Scopecat workspace

Start with `notebooks/01_first_run.py`: one virtual thermometer measurement.
Then use `notebooks/02_edit_scan.py`: edit a request, scan, retain an analysis,
and reopen the same run. Both use the project daemon and its GUI.

| Files | Purpose |
| --- | --- |
| `notebooks/` | Your interactive work; rerunning acquisition creates a new run |
| `src/scopecat_lab/authored/` | Your experiments and analysis; refresh after edits |
| Application, backend and configuration modules | Provided project setup |
| `scopecat.toml` | Project composition and source ownership |
| `.scopecat/` | Runtime data, retained source, receipts and logs; do not edit by hand |

This is a small virtual workspace, not a copy of the reference integration lab.
The only instrument is a virtual thermometer. `repetitions` is a starter scalar
configuration example; the synthetic scan uses its explicit request inputs.
Keep your environment and source with data backups. Updating Scopecat does not
rewrite this workspace or migrate its retained database.

For an installed pilot, run `scopecat start .` then `scopecat open .`.
From a framework source checkout, supply the built GUI using `--static-dir`.
Use `--api-only` only when you intentionally do not need the GUI.
""",
    "src/scopecat_lab/authored/__init__.py": '''\
"""Local experiments and analyses, captured by author refresh."""
''',
    "src/scopecat_lab/authored/signal.py": '''\
"""A synthetic response to explore the framework; no hardware calibration claim."""

from dataclasses import dataclass
from typing import Annotated, cast

import scopecat as sc
from scopecat.measurements.dataset import Dataset


def response(position: float, center: float) -> float:
    return 1.0 / (1.0 + (position - center) ** 2)


@sc.experiment(id="signal")
def signal(
    experiment: sc.ExperimentContext,
    center: float = 0.0,
    *,
    position: Annotated[
        sc.Input[float], sc.ControlSpec(title="Position", scannable=True)
    ] = 0.0,
) -> sc.ValueRef[float]:
    return cast(
        "sc.ValueRef[float]",
        experiment.compute(fn=response, position=position, center=center),
    )


@dataclass(frozen=True)
class Summary:
    mean: float
    points: int


@sc.analysis_function
def summarize(data: Dataset) -> Summary:
    values = cast("tuple[float, ...]", data["result"].require_values())
    return Summary(mean=sum(values) / len(values), points=len(values))
''',
    "notebooks/02_edit_scan.py": '''\
"""Edit a request and retain an analysis without the reference lab or devices."""

from pathlib import Path
from urllib.parse import urlencode

import scopecat as sc
from scopecat.daemon.endpoint import resolve_daemon_endpoint

# %% Setup: load this workspace's local application and import its declarations.
project = sc.open_project(Path(__file__).resolve().parents[1])
_ = project.load_application()
from scopecat_lab.authored.signal import Summary, signal

# %% Edit the request. Arrays become a scan only through sc.Scan.
request = signal(center=0.0)
request.values["position"] = sc.Scan([-1.0, 0.0, 1.0])
alternative = request.copy()
alternative.values["center"] = 0.25

with project.authoring() as author:
    prepared = author.prepare(request)
    job = prepared.run()
    run = job.wait(timeout=120).result()
    report = author.analyze_as(
        run.id, "scopecat_lab.authored.signal:summarize", Summary
    )
    # Reopen existing evidence. Calling prepared.run() again would acquire again.
    reopened = author.reopen(job.receipt).wait(timeout=120).result()
    print({"run_id": reopened.id, "points": report.value.points,
           "mean": report.value.mean, "analysis_id": report.publication.id})
print(resolve_daemon_endpoint(project.root) + "/?" + urlencode({"run": run.id}))
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
