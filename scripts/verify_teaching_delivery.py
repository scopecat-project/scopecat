"""在无仓库导入路径、空缓存的独立环境中验收教学交付。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from time import perf_counter
from typing import cast


def verify(bundle: Path, destination: Path) -> None:
    from lab_tools.bundle import configure_console

    configure_console()
    bundle, destination = bundle.resolve(), destination.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.update(
        UV_OFFLINE="1",
        UV_CACHE_DIR=str(destination / "empty-cache"),
        PYTHONUTF8="1",
    )
    phase = perf_counter()
    help_evidence = destination / "help"
    # One installed application owns practice, author preparation and recovery.
    bootstrap = help_evidence / "application-python"
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [
            sys.executable,
            str(Path(__file__).with_name("verify_installed_help_kernels.py")),
            str(bundle),
            str(help_evidence),
        ],
        cwd=destination,
        env=env,
        check=True,
    )
    help_receipt = cast(
        "dict[str, object]",
        json.loads((help_evidence / "acceptance.json").read_text(encoding="utf-8")),
    )
    application_checks = help_receipt.get("application_checks")
    if (
        help_receipt.get("result") != "passed"
        or not isinstance(application_checks, dict)
        or cast("dict[str, object]", application_checks).get("result") != "passed"
    ):
        raise RuntimeError(
            "Installed application checks must pass in this Help invocation"
        )
    env["UV_CACHE_DIR"] = str(help_evidence / "empty-cache")
    phases = {"installed_help_and_group_recovery": perf_counter() - phase}
    phase = perf_counter()
    python = bootstrap / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    console = python.with_name("scopecat.exe" if os.name == "nt" else "scopecat")
    for command_args in (
        ("--help",),
        ("app", "--help"),
        ("notebook", "--help"),
        ("teach", "--help"),
    ):
        subprocess.run(  # noqa: S603 - installed public entry, no side effects
            [str(console), *command_args],
            cwd=destination,
            env=env,
            check=True,
        )
    phases["console_help"] = perf_counter() - phase
    phase = perf_counter()
    # Exercise the public CLI replacement with the existing application's ordinary
    # author operations. No lesson service or generated editor task is involved.
    project = destination / "编辑器 源码"
    subprocess.run(  # noqa: S603 - installed public entry
        [str(console), "init", str(project), "--topic", "compute"],
        cwd=destination,
        env=env,
        check=True,
    )
    material = (
        Path(__file__).resolve().parents[1]
        / "packages/lab-teaching/src/lab_teaching/course_material"
    )
    for target, resource in (
        ("notebooks/compute.ipynb", "lessons/compute.ipynb"),
        ("src/workspace_app.py", "lessons/workspace_app.py.txt"),
        ("src/my_experiment/teaching.py", "lessons/compute_experiment.py.txt"),
        ("src/my_experiment/parameters.py", "lessons/parameters.py.txt"),
        ("src/my_experiment/setup.py", "lessons/parameters_setup.py.txt"),
        ("src/my_experiment/response.py", "lessons/response.py.txt"),
        ("src/my_experiment/group_analysis.py", "group_analysis.py"),
        ("src/my_experiment/result_types.py", "result_types.py"),
    ):
        assert (project / target).read_bytes() == (material / resource).read_bytes(), (
            f"Installed CLI material differs from checkout: {target}"
        )
    assert not (project / ".vscode/tasks.json").exists()
    note = project / "notebooks/my-notes.md"
    note.write_text("Keep my notes", encoding="utf-8")
    source = project / "src/my_experiment/teaching.py"
    source.write_bytes(source.read_bytes() + b"\n# My source edit\n")
    retained = source.read_bytes()
    for action in (
        "create-author-environment",
        "register-source",
        "prepare-author-environment",
        "create-author-environment",
    ):
        subprocess.run(  # noqa: S603 - existing application author commands
            [
                str(console),
                "app",
                "--home",
                str(help_evidence / "recovered-application"),
                "--action",
                action,
                "--workspace",
                str(project),
            ],
            cwd=project,
            env=env,
            check=True,
        )
    assert source.read_bytes() == retained
    assert note.read_text(encoding="utf-8") == "Keep my notes"
    assert not (project / ".scopecat/daemon.json").exists()
    phases["cli_author_source_and_preparation"] = perf_counter() - phase
    (destination / "acceptance.json").write_text(
        json.dumps(
            {
                "build_id": json.loads(
                    (bundle / "bundle.json").read_text(encoding="utf-8")
                )["build_id"],
                "software": "passed",
                "phase_seconds": phases,
                "cli_author_preparation": "passed",
                "grouped_carrier": "same-application Help and explicit recovery",
                "human": "not-evaluated",
                "physical": "not-evaluated",
                "practice": "synthetic scan and manual decision",
                "same_service_cleanup": "passed",
                "no_management_service": "passed",
                "managed_cleanup": "passed",
                "installed_application": "passed",
                "installed_application_carrier": "shared Help application",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        "离线作者准备、Help 教材重开与恢复、错误内核、应用启停与练习清理验收通过",
        flush=True,
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
