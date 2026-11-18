"""Ordinary config-free demands through declared choices and real simulation."""

import os
import subprocess
import sys
from pathlib import Path

SCRIPT = '''
from pathlib import Path
import importlib, json, sys
import scopecat as sc
from scopecat_server.lifecycle import initialize_project, start_project, stop_project
from scopecat.records.device import DeviceConnection
from scopecat.records.config import VirtualInstrumentConnection
from scopecat.records.instrument_bindings import (
    InstrumentBindingChoice,
    InstrumentBindingPlan,
)
from scopecat.records.execution_scenario import SoftwareExecutionScenario
from scopecat.records.launch_rejection import AuthorLaunchRejected

root = Path(sys.argv[1])
project = initialize_project(root)
backend = root / "src/scopecat_lab/backend.py"
backend.write_text(
    backend.read_text()
    .replace(
        "from scopecat_instruments import ConfiguredInstrumentProvider",
        "from reference_lab.provider import ReferenceLabProvider",
    )
    .replace("ConfiguredInstrumentProvider(seed=0)", "ReferenceLabProvider(seed=0)")
)
source = root / "src/scopecat_lab/authored/capture.py"
source.write_text("""import scopecat as sc
from scopecat_instruments import (dc_bias, DCBiasReadbackProducts,
    network_sweep, NetworkSweepProducts)
@sc.experiment
def capture(experiment: sc.ExperimentContext) -> DCBiasReadbackProducts:
    device=dc_bias(experiment)
    device.ensure(target_voltage=sc.Quantity(0.2,'V'))
    return device.readback()
@sc.experiment
def vna(experiment: sc.ExperimentContext) -> NetworkSweepProducts:
    device=network_sweep(experiment)
    device.ensure(points=3)
    return device.sweep()
""")
start_project(project)
try:
    with project.authoring() as author:
        sys.path.insert(0, str(root / "src"))
        author.refresh()
        importlib.import_module("scopecat_lab.authored.parameters").open_parameters(
            author
        )
        author.use(setup=None)
        capture = importlib.import_module("scopecat_lab.authored.capture").capture
        request = capture()
        required = author.binding_requirements(request)
        assert required.supported and len(required.requirements) == 1
        assert required.requirements[0].role_id is None
        options = author.setup.binding_options(required)
        assert not options.candidates
        assert "no_matching_endpoint" in [d.code for d in options.diagnostics]
        try:
            author.prepare(request)
        except AuthorLaunchRejected as error:
            assert error.diagnostic.bindings == required
        else:
            raise AssertionError("missing binding admitted")
        driver = next(
            d
            for d in author.devices.drivers()
            if d.driver_id == "reference_lab.virtual.multichannel_dc_source"
        )
        device = author.devices.register(
            label="Synthetic DC",
            connection=DeviceConnection(
                driver=driver, connection=VirtualInstrumentConnection()
            ),
            device_id="dc",
        )
        options = author.setup.binding_options(required)
        assert [c.component_path for c in options.candidates] == [
            ("channels", "ch1"),
            ("channels", "ch2"),
        ]
        assert "selection_required" in [d.code for d in options.diagnostics]
        scenario = SoftwareExecutionScenario(
            id="binding-simulation",
            label="Synthetic DC",
            model_id=driver.driver_id,
            model_version="v1",
            capabilities=("Synthetic bias readback",),
            limitations=("No physical qualification",),
        )

        def plan_for(candidate):
            return InstrumentBindingPlan(
                requirements=required,
                choices=(
                    candidate.select(
                        run_start="preserve",
                        success_action="release",
                        failure_action="abort_and_release",
                    ),
                ),
                scenario=scenario,
            )

        plan = plan_for(options.candidates[0])
        assert (
            author.setup.check_bindings(
                plan.model_copy(update={"choices": ()})
            ).definition
            is None
        )
        bad = plan.model_copy(
            update={
                "choices": (
                    plan.choices[0].model_copy(
                        update={"component_path": ("channels", "ch9")}
                    ),
                )
            }
        )
        assert author.setup.check_bindings(bad).definition is None
        checked = author.setup.check_bindings(plan)
        assert checked.definition is not None
        assert (
            not checked.definition.topology.entities
            and not checked.definition.routing.roles
        )
        before = author.setup.list()
        prepared = author.prepare(request, instrument_bindings=plan)
        assert (
            author.setup.list() == before
        )  # execution snapshot is not a named reusable setup
        second_plan = plan_for(options.candidates[1])
        second = author.prepare(request, instrument_bindings=second_plan)
        first_job = prepared.run()
        first = first_job.wait(timeout=90).result()
        second_job = second.run()
        changed = second_job.wait(timeout=90).result()
        assert first.config.routing.routes[0].endpoints[0].component_path == (
            "channels",
            "ch1",
        )
        assert changed.config.routing.routes[0].endpoints[0].component_path == (
            "channels",
            "ch2",
        )
        assert list(first.measurements()["actual_voltage"].require_magnitudes("V")) == [
            0.2
        ]
        saved = author.setup.save(checked.definition, name="reusable")
        assert author.setup.get("reusable").ref == saved.ref
        assert author.setup.revision(saved.id).ref == saved.ref
        # A second ordinary instrument uses the same unlabelled, entity-free API.
        vna = importlib.import_module("scopecat_lab.authored.capture").vna
        vnareq = author.binding_requirements(vna())
        vnadriver = next(
            d for d in author.devices.drivers() if d.driver_id == "scopecat.virtual.vna"
        )
        author.devices.register(
            label="Synthetic VNA",
            connection=DeviceConnection(
                driver=vnadriver, connection=VirtualInstrumentConnection()
            ),
            device_id="vna",
        )
        candidate = author.setup.binding_options(vnareq).candidates[0]
        vnaplan = plan_for(candidate).model_copy(
            update={
                "requirements": vnareq,
                "scenario": scenario.model_copy(
                    update={"model_id": vnadriver.driver_id, "label": "Synthetic VNA"}
                ),
            }
        )
        vnarun = (
            author.prepare(vna(), instrument_bindings=vnaplan)
            .run()
            .wait(timeout=90)
            .result()
        )
        assert len(vnarun.measurements()["frequency"].require_magnitudes("Hz")[0]) == 3
        author.devices.update(device, connection=device.revision.content)
        assert author.setup.check_bindings(plan).definition is None
        assert author.setup.get("reusable").ref != saved.ref
        assert author.setup.revision(saved.id).ref == saved.ref
        try:
            author.prepare(request, instrument_bindings=plan)
        except Exception as error:
            assert "revision changed" in str(error), str(error)
        else:
            raise AssertionError("stale device selection prepared")
        receipts = [str(first_job.receipt), str(second_job.receipt)]
        (root / "evidence.json").write_text(
            json.dumps(
                {"runs": [first.id, changed.id, vnarun.id], "receipts": receipts}
            )
        )
finally:
    stop_project(project)
start_project(project)
try:
    with project.authoring() as author:
        for receipt, path in zip(receipts, ("ch1", "ch2"), strict=True):
            old = author.reopen(Path(receipt)).wait(timeout=90).result()
            assert old.config.routing.routes[0].endpoints[0].component_path == (
                "channels",
                path,
            )
            assert list(
                old.measurements()["actual_voltage"].require_magnitudes("V")
            ) == [0.2]
finally:
    stop_project(project)
'''


def test_progressive_instrument_binding_journey(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    environment["XDG_DATA_HOME"] = str(tmp_path / "xdg")
    result = subprocess.run(  # noqa: S603 - fixed repository-owned simulation script
        [sys.executable, "-c", SCRIPT, str(tmp_path / "project")],
        env=environment,
        capture_output=True,
        text=True,
        timeout=240,
        check=False,
    )
    (tmp_path / "journey.stdout").write_text(result.stdout)
    (tmp_path / "journey.stderr").write_text(result.stderr)
    assert result.returncode == 0, result.stdout + result.stderr
