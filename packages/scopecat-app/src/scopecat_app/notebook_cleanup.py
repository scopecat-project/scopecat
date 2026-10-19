"""Explicit cleanup of one enrolled course; source removal is separately chosen."""

from __future__ import annotations

import shutil
import stat
from pathlib import Path
from typing import TYPE_CHECKING

import httpx2
from filelock import FileLock

from scopecat.author_workspaces import LocalAuthorWorkspaces, author_bindings_path
from scopecat.records.data_cleanup import DataCleanupPreview
from scopecat.records.practice import NotebookPracticeClearCommand, PracticeScope
from scopecat_tutorials.lessons import LessonTopic

from .notebook_journey import NotebookJourney, current, receipt_path, save_journey

if TYPE_CHECKING:
    from .application_runtime import ApplicationRuntime


def _endpoint(runtime: ApplicationRuntime) -> str:
    endpoint = runtime.status().record
    if endpoint is None:
        raise ValueError("应用服务不可用，请恢复后重试")
    return endpoint.base_url


def _course(runtime: ApplicationRuntime, topic: LessonTopic) -> NotebookJourney:
    course = current(runtime, topic)
    if course is None or course.scope_id is None or course.workspace_id is None:
        raise ValueError(
            "旧课程没有可靠归属记录；请通过普通 Data 控件逐项处理，未自动认领"
        )
    return course


def _verify_directory(course: NotebookJourney, path: Path) -> None:
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or path.resolve() != path.absolute()
        or course.directory_device != info.st_dev
        or not course.directory_inode
        or course.directory_inode != info.st_ino
    ):
        raise ValueError("课程目录已移动、替换或变成链接；未删除，请手动检查原目录")


def _quarantine(course: NotebookJourney) -> Path:
    return course.directory.with_name(
        f".{course.directory.name}.clear-{course.scope_id}"
    )


def _verify_registry(
    runtime: ApplicationRuntime, course: NotebookJourney
) -> LocalAuthorWorkspaces:
    registry = LocalAuthorWorkspaces.model_validate_json(
        author_bindings_path(runtime.root).read_bytes()
    )
    registered = next(
        (item for item in registry.items if item.id == course.workspace_id), None
    )
    removed = (
        course.cleanup_command is not None
        and course.cleanup_command.files == "discard"
        and not course.directory.exists()
        and not _quarantine(course).exists()
    )
    if (registered is None and not removed) or (
        registered is not None and registered.root != course.directory.resolve()
    ):
        raise ValueError("课程目录与原登记不符；未删除")
    directory = course.directory.absolute()
    protected = [
        runtime.home.resolve(),
        runtime.root.resolve(),
        registry.service_root.resolve(),
    ]
    for item in registry.items:
        if item.id != course.workspace_id:
            protected.append(item.root.resolve())
            for interpreter in (item.python, *item.retained_pythons):
                protected.extend((interpreter.absolute(), interpreter.resolve()))
    if any(path == directory or path.is_relative_to(directory) for path in protected):
        raise ValueError("此目录仍包含应用或其他作者环境所需文件；未删除")
    return registry


def preview(runtime: ApplicationRuntime, topic: LessonTopic) -> dict[str, object]:
    course = _course(runtime, topic)
    response = httpx2.get(
        f"{_endpoint(runtime)}/api/v1/notebook-practice/{course.scope_id}/preview",
        timeout=30,
        trust_env=False,
    )
    response.raise_for_status()
    result = DataCleanupPreview.model_validate_json(response.content)
    response = httpx2.get(
        f"{_endpoint(runtime)}/api/v1/practice/{course.scope_id}",
        timeout=30,
        trust_env=False,
    )
    response.raise_for_status()
    scope = PracticeScope.model_validate_json(response.content)
    file_error = None
    try:
        _verify_registry(runtime, course)
        target = (
            _quarantine(course) if _quarantine(course).exists() else course.directory
        )
        if target.exists() or course.cleanup_command is None:
            _verify_directory(course, target)
    except (ValueError, OSError) as error:
        file_error = str(error)
    return {
        "preview": result.model_dump(mode="json"),
        "files_error": file_error,
        "files": scope.file_disposition,
    }


def clear(
    runtime: ApplicationRuntime,
    topic: LessonTopic,
    command: NotebookPracticeClearCommand,
) -> dict[str, object]:
    receipt = receipt_path(runtime, topic)
    with FileLock(receipt.with_suffix(".lock"), timeout=30):
        course = _course(runtime, topic)
        if course.cleanup_command is not None and course.cleanup_command != command:
            raise ValueError("请使用原清理预览和文件选项继续")
        if course.cleared:
            return course.view()
        quarantine = course.directory.with_name(
            f".{course.directory.name}.clear-{course.scope_id}"
        )
        if command.files == "discard":
            _verify_registry(runtime, course)
            if (
                quarantine.exists()
                or course.directory.exists()
                or course.cleanup_command is None
            ):
                _verify_directory(
                    course, quarantine if quarantine.exists() else course.directory
                )
        response = httpx2.post(
            f"{_endpoint(runtime)}/api/v1/notebook-practice/{course.scope_id}/clear",
            content=command.model_dump_json(),
            headers={"Content-Type": "application/json"},
            timeout=60,
            trust_env=False,
        )
        response.raise_for_status()
        scope = PracticeScope.model_validate_json(response.content)
        if scope.state != "cleared":
            raise ValueError(scope.cleanup_error or "课程数据清理尚未完成")
        course = course.model_copy(update={"cleanup_command": command, "ready": False})
        save_journey(receipt, course)
        if command.files == "discard":
            registry_path = author_bindings_path(runtime.root)
            with FileLock(registry_path.parent / "author-workspaces.lock", timeout=30):
                registry = _verify_registry(runtime, course)
                if not quarantine.exists() and course.directory.exists():
                    _verify_directory(course, course.directory)
                    course.directory.rename(quarantine)
                # Check the moved object before deletion, including on retry.
                if quarantine.exists():
                    _verify_directory(course, quarantine)
                    shutil.rmtree(quarantine)
                updated = registry.model_copy(
                    update={
                        "items": tuple(
                            item
                            for item in registry.items
                            if item.id != course.workspace_id
                        )
                    }
                )
                staged = registry_path.with_suffix(".tmp")
                staged.write_text(updated.model_dump_json(indent=2), encoding="utf-8")
                staged.replace(registry_path)
        course = course.model_copy(update={"cleared": True, "ready": False})
        save_journey(receipt, course)
        return course.view()
