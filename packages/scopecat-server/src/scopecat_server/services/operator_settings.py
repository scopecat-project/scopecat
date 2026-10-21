"""Editable attribution defaults, never authentication or execution authority."""

import os
from pathlib import Path
from tempfile import NamedTemporaryFile

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, field_validator

from scopecat_server.errors import BackendConflict

OPERATOR_SETTINGS_FILE = "operator-settings.json"


class OperatorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(default=0, ge=0)
    operator: str = Field(default="", max_length=4096)

    @field_validator("operator")
    @classmethod
    def trim_operator(cls, value: str) -> str:
        return value.strip()


class OperatorSettingsService:
    def __init__(self, data_root: Path) -> None:
        self.path = data_root / OPERATOR_SETTINGS_FILE

    def read(self) -> OperatorSettings:
        try:
            return OperatorSettings.model_validate_json(self.path.read_bytes())
        except FileNotFoundError:
            return OperatorSettings()

    def save(self, command: OperatorSettings) -> OperatorSettings:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with FileLock(self.path.with_suffix(".lock"), timeout=5):
            current = self.read()
            if command.revision != current.revision:
                raise BackendConflict("Operator setting changed; reload before saving")
            saved = command.model_copy(update={"revision": current.revision + 1})
            temporary: Path | None = None
            try:
                with NamedTemporaryFile(
                    mode="w",
                    encoding="utf-8",
                    dir=self.path.parent,
                    prefix=".operator-settings-",
                    suffix=".tmp",
                    delete=False,
                ) as stream:
                    temporary = Path(stream.name)
                    stream.write(saved.model_dump_json())
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            return saved
