"""Qualify the actual testkit wheel outside its repository layout."""

import shutil
import subprocess
import sys
from pathlib import Path

from scopecat.config.documents import load_config_snapshot_document
from scopecat_testkit.config_fixtures import simple_scan_config
from scopecat_testkit.paths import CORE_FIXTURE_DIR, REPO_ROOT


def test_synthetic_preset_matches_document_fixture() -> None:
    assert simple_scan_config() == load_config_snapshot_document(
        CORE_FIXTURE_DIR / "config-snapshot.json"
    )


def test_wheel_runs_adapter_qualification_without_checkout_fixtures(
    tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    assert uv is not None
    built = subprocess.run(  # noqa: S603 - Trusted build tool and repository package.
        [
            uv,
            "build",
            "--wheel",
            "--out-dir",
            str(tmp_path),
        ],
        cwd=REPO_ROOT / "testing/scopecat-testkit",
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    [wheel] = tmp_path.glob("scopecat_testkit-*.whl")
    # Load only the built testkit artifact; -I excludes checkout/PYTHONPATH injection.
    result = subprocess.run(  # noqa: S603 - Fixed qualification script, built artifact.
        [sys.executable, "-I", "-c", _QUALIFY, str(wheel), str(tmp_path)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, built.stdout + result.stdout + result.stderr


_QUALIFY = """
import sys
from pathlib import Path
wheel, root = map(Path, sys.argv[1:])
sys.path.insert(0, str(wheel))
import scopecat_testkit
assert str(wheel) in scopecat_testkit.__file__
from scopecat_testkit.authoring import load_config as author_config
from scopecat_testkit.instrument_drivers import load_config as instrument_config
from scopecat_testkit.workflow_fixtures import load_config as workflow_config
from scopecat_testkit.connection_residency import (
    ResidencyProbe, VolatileProgramProvider, VolatileProgramTarget,
    check_connection_residency, residency_config, residency_experiment,
)
from scopecat_testkit.instrument_host import compose_test_instruments
from scopecat_testkit.server.execution import execute_invocation_run
assert author_config() == instrument_config() == workflow_config()
changed = instrument_config()
changed.system.instrument_registry.instruments.clear()
assert instrument_config().instrument_registry.instruments
probe = ResidencyProbe(root / "device")
config = residency_config()
composition = compose_test_instruments(
    config=config, provider=VolatileProgramProvider(probe),
    domain_compiler=VolatileProgramTarget(),
)
def run(points):
    return execute_invocation_run(
        config=config, experiment=residency_experiment(points),
        system=composition.system, instrument_backend=composition.backend,
        project_root=root / "project",
    )
check_connection_residency(run, probe)
assert "scopecat_testkit.paths" not in sys.modules
"""
