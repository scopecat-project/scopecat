"""Explicit cleanup of managed exercises; current exercises and live processes stay."""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict, cast

import psutil

from .project import METADATA


class ProcessInfo(TypedDict):
    cmdline: list[str] | None
    cwd: str | None
    exe: str | None


def read_record(path: Path) -> dict[str, object]:
    value = cast("object", json.loads(path.read_text(encoding="utf-8")))
    if not isinstance(value, dict):
        raise ValueError(f"损坏的沙盒记录: {path}")
    return cast("dict[str, object]", value)


@dataclass(frozen=True)
class OldSandbox:
    root: Path
    size: int


def old_sandboxes(home: Path, active_key: str) -> list[OldSandbox]:
    base = home.resolve() / "sandboxes"
    if base.is_symlink():
        raise ValueError("沙盒目录不能指向外部目录")
    result: list[OldSandbox] = []
    for root in sorted(base.glob("*/*/*")):
        if (
            not root.is_dir()
            or len(root.name) != 32
            or any(c not in "0123456789abcdef" for c in root.name)
            or root.is_symlink()
            or root.parent.is_symlink()
            or root.parent.parent.is_symlink()
        ):
            continue
        try:
            metadata = read_record(root / METADATA)
            pointer_path = root.parent / "current.json"
            pointer = read_record(pointer_path) if pointer_path.exists() else {}
        except OSError, ValueError:
            continue
        if metadata.get("kind") != "teaching":
            continue
        if (
            root.parent.parent.name == active_key
            and pointer.get("generation") == root.name
        ):
            continue
        size = sum(
            p.stat().st_size
            for p in root.rglob("*")
            if p.is_file() and not p.is_symlink()
        )
        result.append(OldSandbox(root, size))
    return result


def in_use(root: Path) -> bool:
    prefix = str(root.resolve())
    for process in psutil.process_iter(["cmdline", "cwd", "exe"]):
        info = cast("ProcessInfo", cast("object", process.info))
        values = [*(info["cmdline"] or []), info["cwd"] or "", info["exe"] or ""]
        if any(prefix in value for value in values):
            return True
    return False


def remove_old_sandbox(home: Path, active_key: str, root: Path) -> None:
    if root not in {item.root for item in old_sandboxes(home, active_key)}:
        raise ValueError("只能清理列表中的旧教学副本；当前练习保留")
    if in_use(root):
        raise ValueError("副本仍有服务或 Notebook 内核运行；关闭后再清理")
    shutil.rmtree(root)
    pointer = root.parent / "current.json"
    if pointer.exists() and read_record(pointer).get("generation") == root.name:
        pointer.unlink()


def cleanup_menu(home: Path, active_key: str) -> None:
    candidates = old_sandboxes(home, active_key)
    if not candidates:
        print("没有可清理的旧练习。当前版本正在使用的各专题副本始终保留。")
        return
    print("旧练习可能包含你的修改。先关闭内核和服务；删除后不可恢复。")
    for number, item in enumerate(candidates, 1):
        print(f"{number}. {item.size / 1024**2:.1f} MiB  {item.root}")
    selected = input("输入要删除的副本编号 (回车取消): ").strip()
    if not selected:
        return
    if not selected.isdecimal() or not 1 <= int(selected) <= len(candidates):
        raise ValueError("无效副本编号")
    root = candidates[int(selected) - 1].root
    if input(f"确认删除 {root.name}？输入 DELETE: ").strip() != "DELETE":
        return
    remove_old_sandbox(home, active_key, root)
    print("已删除所选旧练习。交付包和其他副本保留。")
