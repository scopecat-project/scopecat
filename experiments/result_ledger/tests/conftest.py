"""Separate ordinary experiment tests from the pinned production control."""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--run-pinned-baseline",
        action="store_true",
        default=False,
        help="Run the production control with packages installed from fixed f2e514a1",
    )


@pytest.fixture
def pinned_baseline(request):
    if not request.config.getoption("--run-pinned-baseline"):
        pytest.skip(
            "pinned baseline control is opt-in: use --run-pinned-baseline with "
            "the clean f2e514a1 checkout's interpreter (see README)"
        )
