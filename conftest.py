"""Keep automated plots off the user's desktop, including in test subprocesses."""

import os

import pytest

# Set this before collection imports pyplot. Inheriting the environment also
# keeps notebook, analysis and worker subprocesses on the non-interactive backend.
os.environ["MPLBACKEND"] = "Agg"


@pytest.fixture(autouse=True)
def isolated_application_locator(tmp_path, monkeypatch):
    """Each test and its child kernels share an isolated per-user locator."""
    monkeypatch.setenv("SCOPECAT_LOCATOR_HOME", str(tmp_path / "locator"))
    monkeypatch.delenv("SCOPECAT_APPLICATION_ROOT", raising=False)
