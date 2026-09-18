"""在无仓库导入路径、空缓存的独立环境中验收教学交付。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TypedDict, cast


class Task(TypedDict):
    label: str
    command: str
    args: list[str]


class Editor(TypedDict):
    tasks: list[Task]


class Cell(TypedDict):
    cell_type: str
    source: list[str]


class Notebook(TypedDict):
    cells: list[Cell]


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
    bootstrap = destination / "bootstrap"
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [sys.executable, str(bundle / "install.py"), str(bootstrap)],
        cwd=destination,
        env=env,
        check=True,
    )
    python = bootstrap / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    command = [str(python), "-m", "lab_tools.cli"]
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [*command, "verify", str(destination / "中文 教材")],
        cwd=destination,
        env=env,
        check=True,
    )
    project = destination / "编辑器 准备"
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [*command, "create", str(project)], cwd=destination, env=env, check=True
    )
    tasks = cast(
        "Editor",
        json.loads((project / ".vscode/tasks.json").read_text(encoding="utf-8")),
    )
    task = next(t for t in tasks["tasks"] if t["label"] == "首次准备项目环境")
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [
            task["command"],
            *[a.replace("${workspaceFolder}", str(project)) for a in task["args"]],
        ],
        cwd=project,
        env=env,
        check=True,
    )
    notebook = cast(
        "Notebook",
        json.loads((project / "notebooks/start.ipynb").read_text(encoding="utf-8")),
    )
    cell = next(c for c in notebook["cells"] if c["cell_type"] == "code")
    result = subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [str(python), "-c", "".join(cell["source"])],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode == 0 or "Select Kernel" not in result.stderr:
        raise RuntimeError(f"错误内核未正确拒绝: {result.stdout}\n{result.stderr}")
    home = destination / "中文 沙盒中心"
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [sys.executable, str(bundle / "install.py"), "--home", str(home)],
        cwd=destination,
        env=env,
        check=True,
    )
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [sys.executable, str(bundle / "install.py"), "--home", str(home)],
        cwd=destination,
        env=env,
        check=True,
    )
    receipts = list((home / "releases").glob("*/runtime/scopecat-lab-delivery.json"))
    assert len(receipts) == 1
    receipt = cast(
        "dict[str, str]", json.loads(receipts[0].read_text(encoding="utf-8"))
    )
    assert Path(receipt["bundle"]).is_relative_to(home)
    # The retained bundle lives inside home; users can disconnect transfer media.
    launcher = home / "lab.py"
    host_instance: str | None = None
    for topic in ("parameters", "compute", "refresh", "groups"):
        subprocess.run(  # noqa: S603 - explicit local tool and argument list
            [str(python), str(launcher), topic, "--verify"],
            cwd=destination,
            env=env,
            check=True,
        )
        from lab_tools.host_client import HostClient, HostRecord

        record = HostRecord.model_validate_json(
            (home / "host/endpoint.json").read_text(encoding="utf-8")
        )
        if host_instance is None:
            host_instance = record.instance
        assert record.instance == host_instance
    manager = HostClient(record)
    current = next((home / "sandboxes").glob("*/parameters/current.json"))
    before = current.read_text(encoding="utf-8")
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [str(python), str(launcher), "parameters", "--stop"],
        cwd=destination,
        env=env,
        check=True,
    )
    assert current.read_text(encoding="utf-8") == before
    subprocess.run(  # noqa: S603 - explicit local tool and argument list
        [str(python), str(launcher), "parameters", "--reset", "--verify"],
        cwd=destination,
        env=env,
        check=True,
    )
    assert current.read_text(encoding="utf-8") != before
    from lab_tools.host_operations import Command

    old_generation = cast("dict[str, str]", json.loads(before))["generation"]
    deletion = Command(action="delete", workspace=old_generation)
    result = manager.wait(manager.submit(deletion))
    assert manager.submit(deletion).command.id == result.command.id
    assert not (current.parent / old_generation).exists()
    assert all(item.status == "succeeded" for item in manager.state().operations)
    manager.shutdown()
    (destination / "acceptance.json").write_text(
        json.dumps(
            {
                "build_id": json.loads(
                    (bundle / "bundle.json").read_text(encoding="utf-8")
                )["build_id"],
                "software": "passed",
                "human": "not-evaluated",
                "physical": "not-evaluated",
                "topics": ["parameters", "compute", "refresh", "groups"],
                "reinstall": "passed",
                "reset": "passed",
                "single_host": "passed",
                "managed_cleanup": "passed",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        "离线安装、教材重开、编辑器准备、错误内核、四专题与沙盒重置验收通过", flush=True
    )


if __name__ == "__main__":
    verify(Path(sys.argv[1]), Path(sys.argv[2]))
