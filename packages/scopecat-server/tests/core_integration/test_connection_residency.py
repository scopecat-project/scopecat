"""Run the adapter-facing contract through the actual core execution pipeline."""

from pathlib import Path

from scopecat.records.run import RunSnapshot
from scopecat_testkit.connection_residency import (
    ResidencyProbe,
    VolatileProgramProvider,
    VolatileProgramTarget,
    check_connection_residency,
    residency_config,
    residency_experiment,
)
from scopecat_testkit.instrument_host import compose_test_instruments
from scopecat_testkit.server.execution import execute_invocation_run


def test_public_target_connection_residency_contract(tmp_path: Path) -> None:
    probe = ResidencyProbe(tmp_path / "device")
    config = residency_config()
    composition = compose_test_instruments(
        config=config,
        provider=VolatileProgramProvider(probe),
        domain_compiler=VolatileProgramTarget(),
    )

    def run(points: int) -> RunSnapshot:
        return execute_invocation_run(
            config=config,
            experiment=residency_experiment(points),
            system=composition.system,
            instrument_backend=composition.backend,
            project_root=tmp_path / "project",
        )

    check_connection_residency(run, probe)
    # This direct runner closes its instrument connection after every run.
    connections = [
        event.connection for event in probe.events() if event.operation == "connect"
    ]
    assert len(set(connections)) == len(connections) == 5
    for connection in connections:
        operations = [
            event.operation
            for event in probe.events()
            if event.connection == connection
        ]
        assert operations[:2] == ["connect", "setup"]
        first_setup = next(
            event
            for event in probe.events()
            if event.connection == connection and event.operation == "setup"
        )
        assert first_setup.content is None
        assert operations[-1] == "disconnect"
