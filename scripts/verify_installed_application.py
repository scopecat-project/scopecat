"""Qualify the installed application without repository imports or instruments."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import httpx2

from lab_tools.application_runtime import ApplicationRuntime
from scopecat_server.scaffold import write_author_scaffold  # noqa: TID251


def files(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def verify(home: Path, destination: Path, gui: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    source = destination / "实验代码"
    write_author_scaffold(source)
    (source / "owner-notes.txt").write_text("保留实验记录\n", encoding="utf-8")
    runtime = ApplicationRuntime(home)
    selected = runtime.configure(static_dir=gui)
    identity = runtime.register_source(source)
    retained = files(source)

    def check_workbench() -> None:
        record = runtime.start()
        assert runtime.start() == record
        with httpx2.Client(
            base_url=record.base_url, trust_env=False, timeout=30
        ) as client:
            page = client.get("/")
            assert page.status_code == 200
            assert page.content == (gui / "index.html").read_bytes()
            runs = client.get("/api/v1/runs")
            assert runs.status_code == 200
            assert runs.json()["items"] == []

    try:
        check_workbench()
        runtime.stop()
        assert runtime.status().state == "stopped"
        runtime.select(runtime.qualify(selected.python, selected.static_dir))
        assert runtime.source(source) == identity
        assert files(source) == retained
        runtime = ApplicationRuntime(home)
        check_workbench()
        runtime.stop()
        assert runtime.source(source) == identity
        assert files(source) == retained
        assert not (home / "host/services.sqlite").exists()
        (destination / "acceptance.json").write_text(
            json.dumps(
                {
                    "software": "passed",
                    "human": "not-evaluated",
                    "physical": "not-evaluated",
                    "source_id": identity,
                    "environment": selected.environment,
                    "direct_workbench_without_acquisition": "passed",
                    "requalification_preserves_files_and_identity": "passed",
                    "application_reopen": "passed",
                    "one_runtime_without_manager": "passed",
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    finally:
        runtime.stop()


if __name__ == "__main__":
    verify(
        Path(sys.argv[1]).resolve(),
        Path(sys.argv[2]).resolve(),
        Path(sys.argv[3]).resolve(),
    )
