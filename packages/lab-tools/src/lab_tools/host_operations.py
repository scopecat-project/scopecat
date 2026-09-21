"""Durable local teaching operations; workers own their installed environment."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from threading import Thread

from pydantic import BaseModel

from lab_teaching.lessons import TOPICS

from .cleanup import in_use, read_record, remove_old_sandbox
from .host_models import Command as Command
from .host_models import Operation as Operation
from .host_store import Operations as Operations
from .project import METADATA
from .sandboxes import sandbox_key, select_project


class Workspace(BaseModel):
    id: str
    topic: str
    title: str
    root: str
    version: str
    current: bool
    active_version: bool
    in_use: bool
    deletable: bool


def workspaces(home: Path, key: str) -> list[Workspace]:
    base = home.resolve() / "sandboxes"
    if base.is_symlink():
        raise ValueError("沙盒目录不能指向外部目录")
    result: list[Workspace] = []
    for root in sorted(base.glob("*/*/*")):
        if (
            not root.is_dir()
            or root.parent.name not in TOPICS
            or len(root.name) != 32
            or any(c not in "0123456789abcdef" for c in root.name)
            or any(p.is_symlink() for p in (root, root.parent, root.parent.parent))
        ):
            continue
        try:
            if read_record(root / METADATA).get("kind") != "teaching":
                continue
            pointer = root.parent / "current.json"
            current = (
                pointer.exists() and read_record(pointer).get("generation") == root.name
            )
        except OSError, ValueError:
            continue
        active = root.parent.parent.name == key
        busy = in_use(root)
        result.append(
            Workspace(
                id=root.name,
                topic=root.parent.name,
                title=TOPICS[root.parent.name],
                root=str(root),
                version=root.parent.parent.name,
                current=current,
                active_version=active,
                in_use=busy,
                deletable=not (active and current) and not busy,
            )
        )
    return result


def owned_workspace(home: Path, key: str, identity: str) -> Workspace:
    matches = [item for item in workspaces(home, key) if item.id == identity]
    if len(matches) != 1:
        raise ValueError("未找到此受管理的教学练习；请刷新列表")
    return matches[0]


def launch(home: Path, source: Path | None, command: Command) -> Operation:
    if command.action in (
        "setup",
        "service_start",
        "service_stop",
        "service_remove",
        "service_recheck",
        "service_update",
    ):
        from .services import Services

        with Services(home).lock:
            return _launch(home, source, command)
    return _launch(home, source, command)


def _launch(home: Path, source: Path | None, command: Command) -> Operation:
    operations = Operations(home)
    previous = operations.find(command.id)
    if previous is not None:
        if previous.command != command:
            raise ValueError("同一操作编号不能用于不同请求")
        return previous
    if (command.action == "service_update") != (command.environment_bundle is not None):
        raise ValueError("只有环境更新操作需要指定交付目录")
    if command.action == "setup":
        if (
            command.setup is None
            or command.service is not None
            or command.topic is not None
            or command.workspace is not None
            or command.reset
        ):
            raise ValueError("请选择创建或接入实验目录")
    elif command.setup is not None:
        raise ValueError("只有首次接入操作可以指定实验目录")
    elif command.action in (
        "service_start",
        "service_stop",
        "service_remove",
        "service_recheck",
        "service_update",
    ):
        from .services import Services

        if (
            command.service is None
            or command.topic is not None
            or command.workspace is not None
            or command.reset
        ):
            raise ValueError("请选择已登记的实验服务编号")
        Services(home).get(command.service)
    elif command.service is not None:
        raise ValueError("教学操作不能选择实验服务")
    elif command.action in ("open", "verify"):
        if command.topic not in TOPICS or command.workspace is not None:
            raise ValueError("请选择有效专题")
    elif command.workspace is None or command.reset or command.topic is not None:
        raise ValueError("请选择受管理练习的编号")
    operation, created = operations.begin(command)
    if not created:
        return operation
    args = [sys.executable, "-m", "lab_tools.host_worker", str(home), command.id]
    if source is not None:
        args.extend(("--source", str(source)))
    try:
        with (operations.directory / f"{command.id}.log").open("ab") as log:
            child = subprocess.Popen(  # noqa: S603 - fixed worker entry, no user shell
                args,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=sys.platform != "win32",
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP
                if sys.platform == "win32"
                else 0,
            )
        Thread(target=child.wait, daemon=True).start()
    except OSError as error:
        operation.status = "failed"
        operation.detail = str(error)
        operations.save(operation)
    return operation


def execute(home: Path, source: Path | None, command: Command) -> str | None:
    from .notebook import project_python
    from .sandboxes import run

    if command.action == "setup":
        from .first_run import setup
        from .services import Services

        assert command.setup is not None
        service = setup(
            home,
            command.setup,
            static_dir=source / "apps" / "scopecat-ui" / "dist" if source else None,
        )
        services = Services(home)
        services.start(service.id)
        services.remember(service.id)
        return service.id
    if command.action in (
        "service_start",
        "service_stop",
        "service_remove",
        "service_recheck",
        "service_update",
    ):
        from .services import Services

        assert command.service is not None
        services = Services(home)
        if command.action == "service_start":
            services.start(command.service)
        elif command.action == "service_stop":
            services.stop(command.service)
        elif command.action == "service_update":
            assert command.environment_bundle is not None
            services.update_environment(
                command.service,
                Path(command.environment_bundle),
                operation_id=command.id,
            )
        elif command.action == "service_recheck":
            services.recheck(command.service, operation_id=command.id)
        else:
            services.remove(command.service, operation_id=command.id)
        return None
    key = sandbox_key(source)
    if command.action in ("stop", "delete"):
        assert command.workspace is not None
        root = Path(owned_workspace(home, key, command.workspace).root)
        if command.action == "delete":
            remove_old_sandbox(home, key, root)
        else:
            run([str(project_python(root)), "-m", "lab_tools.cli", "stop", str(root)])
        return root.name
    assert command.topic is not None
    if command.reset:
        current = next(
            (
                item
                for item in workspaces(home, key)
                if item.topic == command.topic and item.current and item.active_version
            ),
            None,
        )
        if current is not None and current.in_use:
            raise ValueError("重置前请关闭 Notebook 内核，并在管理页面停止此练习的服务")
    root = select_project(home, command.topic, source=source, reset=command.reset)
    python = str(project_python(root))
    if command.action == "verify":
        args = [python, "-m", "lab_tools.verify_lesson", str(root), command.topic]
    else:
        args = [python, "-m", "lab_tools.cli", "start", str(root)]
    if source is not None:
        args.append("--api-only")
    run(args)
    return root.name
