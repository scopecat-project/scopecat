from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import scopecat as sc
from scopecat.planning.catalog import InstrumentContractCatalog
from scopecat.records.config import config_content_hash, instrument_bindings
from scopecat.sdk.instruments import InstrumentProviderContext

from reference_lab.application import create_application, create_bootstrap
from reference_lab.backend import create_backend
from reference_lab.configuration import (
    EXAMPLE_ROOT,
    bootstrap_config,
)


def test_reference_lab_manifest_discovers_separate_bootstrap_and_application() -> None:
    project = sc.open_project(EXAMPLE_ROOT / "notebooks")
    bootstrap = project.load_bootstrap()

    assert project.root == EXAMPLE_ROOT
    assert project.bootstrap_spec == "reference_lab.application:create_bootstrap"
    assert project.application_spec == "reference_lab.application:create_application"
    assert project.instrument_backend_spec == "reference_lab.backend:create_backend"
    assert bootstrap.setup is not None
    assert bootstrap.parameter_defaults is not None
    assert bootstrap.setup().primary_entity_id == bootstrap_config().primary_entity_id
    assert (
        bootstrap.parameter_defaults().parameters
        == bootstrap_config().parameter_snapshot
    )


def test_reference_lab_daemon_bootstrap_keeps_execution_callbacks_cold(
    tmp_path: Path,
) -> None:
    shutil.copytree(EXAMPLE_ROOT / "config", tmp_path / "config")
    shutil.copytree(
        EXAMPLE_ROOT / "src" / "reference_lab",
        tmp_path / "src" / "reference_lab",
    )
    result = subprocess.run(  # noqa: S603 - fixed interpreter and local test source
        [
            sys.executable,
            "-c",
            f"""
import sys
from pathlib import Path
from scopecat_server.runtime import LocalDaemonRuntime

with LocalDaemonRuntime(
    Path({str(tmp_path)!r}),
    bootstrap_spec="reference_lab.application:create_bootstrap",
):
    pass
forbidden = {{
    "reference_lab.lab",
    "reference_lab.workflows.drag_beta_automatic_publication",
    "reference_lab.workflows.drag_beta_freshness",
    "reference_lab.workflows.drag_beta_procedure",
}}
loaded = forbidden.intersection(sys.modules)
if loaded:
    raise SystemExit(f"bootstrap imported execution modules: {{sorted(loaded)}}")
""",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_reference_lab_application_loads_selected_project_system(
    tmp_path: Path,
) -> None:
    config_dir = tmp_path / "config"
    shutil.copytree(EXAMPLE_ROOT / "config", config_dir)
    infrastructure_path = config_dir / "system-infrastructure.json"
    infrastructure_path.write_text(
        infrastructure_path.read_text().replace(
            '"id": "reference-lab-system"',
            '"id": "selected-reference-lab-system"',
            1,
        )
    )

    application = create_application(tmp_path)
    bootstrap = create_bootstrap(tmp_path)

    assert application.build_experiment_system is not None
    assert bootstrap.setup is not None
    assert bootstrap.parameter_defaults is not None
    selected_config = bootstrap_config(config_dir)
    assert bootstrap.setup().primary_entity_id == selected_config.primary_entity_id
    assert selected_config == bootstrap_config(config_dir)
    backend = create_backend(tmp_path)
    provider = backend.provider
    from reference_lab.provider import ReferenceLabProvider

    assert isinstance(provider, ReferenceLabProvider)
    described = provider.describe(
        InstrumentProviderContext(bindings=instrument_bindings(selected_config))
    )
    catalog = InstrumentContractCatalog(
        config_content_hash=config_content_hash(selected_config),
        provider_id=described.provider_id,
        instruments=described.instruments,
        problems=described.problems,
    )
    system = application.build_experiment_system(selected_config, catalog)
    assert system.instrument_catalog == catalog
    assert system.domain_compiler is not None
