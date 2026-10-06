"""Persistent selection of the one desktop data space."""

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .bundle import managed_path


class SpaceSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    space: str = Field(pattern=r"^[0-9a-f]{32}$")


def selected_home(anchor: Path) -> Path:
    pointer = managed_path(anchor, anchor / "current-space.json")
    if not pointer.exists():
        return anchor
    selection = SpaceSelection.model_validate_json(pointer.read_bytes())
    home = managed_path(anchor, anchor / "spaces" / selection.space)
    if not home.is_dir():
        raise ValueError("当前数据空间不存在；请保留所有目录并联系维护者")
    return home
