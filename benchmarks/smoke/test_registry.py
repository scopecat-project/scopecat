from __future__ import annotations

import json
import subprocess
import sys
from typing import cast


def test_registry_lists_every_classified_case() -> None:
    completed = subprocess.run(
        (sys.executable, "-m", "benchmarks", "list", "--json"),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    cases = cast("list[dict[str, object]]", json.loads(completed.stdout))
    assert [(case["id"], case["kind"]) for case in cases] == [
        ("entity-reads", "component"),
        ("scan-execution", "e2e"),
        ("scale-suite", "e2e"),
        ("adaptive-context", "component"),
        ("list-mode-compiler", "component"),
        ("historical-project", "component"),
        ("quantum-program", "component"),
        ("inspection-index", "micro"),
        ("payload-attachments", "micro"),
    ]
