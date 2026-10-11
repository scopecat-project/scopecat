"""Software acceptance of shared live/offline spectra and repeated T1 analysis."""

import shutil
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from scopecat.application.author_project import AuthorProject
from scopecat.project import load_project
from scopecat_server.lifecycle import start_project, stop_project

from reference_lab.configuration import EXAMPLE_ROOT, initial_parameters
from reference_lab_authors.authored.group_traces import DecayFit, ResonanceMinimum

pytestmark = pytest.mark.usefixtures("reference_lab_author_imports")


def _compare[ResultT](
    author: AuthorProject,
    run_id: str,
    function: str,
    result_type: type[ResultT],
    *,
    by: tuple[str, ...],
    fitting: str,
) -> tuple[ResultT | None, ...]:
    analysis = f"reference_lab_authors.authored.group_traces:{function}"
    follow = author.follow_groups_as(
        run_id, analysis, result_type, by=by, fitting=fitting
    )
    deadline = time.monotonic() + 30
    while True:
        page = follow.poll()
        if page.follow.state != "running":
            break
        assert time.monotonic() < deadline
        time.sleep(0.1)
    assert page.follow.state == "completed", page
    assert page.follow.failed_count == 0, page
    live = follow.results(page)
    offline = author.analyze_groups_as(
        run_id, analysis, result_type, by=by, fitting=fitting
    )
    assert [group.value for group in live] == [group.value for group in offline.groups]
    assert all(group.publication.outputs for group in live)
    return tuple(group.value for group in live)


def test_power_spectroscopy_and_repeated_t1(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    select_reference_source: Callable[[Path], None],
) -> None:
    monkeypatch.delenv("SCOPECAT_DAEMON_URL", raising=False)
    root = tmp_path / "traces"
    root.mkdir()
    for name in ("src", "config"):
        shutil.copytree(EXAMPLE_ROOT / name, root / name)
    shutil.copy2(EXAMPLE_ROOT / "scopecat.toml", root / "scopecat.toml")
    select_reference_source(root)
    project = load_project(root / "scopecat.toml")
    start_project(project)
    try:
        with project.authoring() as author:
            content = initial_parameters()
            revision = author.parameters.save(
                name="traces", catalog=content.catalog, parameters=content.parameters
            )
            author.parameters.create_branch("traces", revision=revision)
            author.use(
                parameter_branch="traces", setup=author.resolve_setup("initial").ref
            )
            author.refresh()
            power = (
                author.prepare("power_trace", scans={"power": [-40, -30]})
                .run()
                .wait()
                .result()
            )
            resonances = _compare(
                author,
                power.id,
                "locate_resonance",
                ResonanceMinimum,
                by=("power",),
                fitting="trace/frequency",
            )
            assert len(resonances) == 2
            assert all(
                value is not None and 4.93 < value.frequency.to("GHz").value < 5.08
                for value in resonances
            )
            t1 = (
                author.prepare(
                    "synthetic_t1",
                    scans={"repeat": [0, 1], "delay": [0, 10, 20, 40, 80]},
                )
                .run()
                .wait()
                .result()
            )
            decays = _compare(
                author, t1.id, "fit_decay", DecayFit, by=(), fitting="delay"
            )
            assert [
                value.lifetime.to("us").value for value in decays if value is not None
            ] == pytest.approx([20, 25])
    finally:
        stop_project(project)
