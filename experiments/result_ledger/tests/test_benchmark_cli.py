"""CLI dependency boundaries, not benchmark performance assertions."""

import json
import subprocess
import sys

import pytest
from experiments.result_ledger.benchmark import ROOT, main


def test_staged_pair_needs_no_baseline_checkout(tmp_path):
    output = tmp_path / "pair"
    absent = tmp_path / "absent-baseline"
    child = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "experiments.result_ledger.benchmark",
            "--comparison",
            "staged-atomic",
            "--baseline-repo",
            str(absent),
            "--directory",
            str(output),
            "--points",
            "4",
            "--samples",
            "2",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert child.returncode == 0, child.stderr
    report = json.loads((output / "results.json").read_text())
    assert len(report["samples"]) == 4
    assert report["baseline_sha"] is None
    assert report["uv_lock_sha256"] is None
    assert not absent.exists()


@pytest.mark.parametrize("mode", [[], ["--worker", "baseline"]])
def test_baseline_requires_explicit_checkout_before_creating_output(tmp_path, mode):
    output = tmp_path / "must-not-exist"
    with pytest.raises(SystemExit) as failure:
        main(["--directory", str(output), *mode])
    assert failure.value.code == 2
    assert not output.exists()
