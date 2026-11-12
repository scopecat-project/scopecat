"""Data-space defaults for new pending-result reservations, not their ownership."""

import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Literal

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from scopecat_server.errors import BackendConflict

PENDING_RESULT_SETTINGS_FILE = "pending-result-settings.json"

type PendingResultStoragePolicy = Literal["memory_only", "allow_temporary_disk"]


class PendingResultSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    revision: int = Field(default=0, ge=0)
    storage_policy: PendingResultStoragePolicy = "memory_only"


class PendingResultCapacity(BaseModel):
    """Application payload charges, not an operating-system RSS measurement."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    memory_bytes: int = Field(ge=0)
    work_bytes: int = Field(ge=0)
    disk_bytes: int = Field(ge=0)
    memory_limit_bytes: int = Field(gt=0)
    work_limit_bytes: int = Field(gt=0)
    disk_limit_bytes: int = Field(gt=0)
    storage_degraded: bool


class PendingResultSettingsService:
    """Serialize settings publication with the runtime's admission snapshots.

    The single daemon owner shares this service with all window requests. A
    reservation keeps the returned immutable settings, not a live preference.
    Existing payloads are never migrated or deleted by saving this file.
    """

    def __init__(self, data_root: Path) -> None:
        self.path = data_root / PENDING_RESULT_SETTINGS_FILE
        self.coordination_lock = RLock()

    def read(self) -> PendingResultSettings:
        with self.coordination_lock:
            try:
                if not stat.S_ISREG(self.path.lstat().st_mode):
                    raise BackendConflict(
                        "Pending-result settings must be a regular file"
                    )
                content = self.path.read_bytes()
            except FileNotFoundError:
                return PendingResultSettings()
            except OSError as error:
                raise BackendConflict(
                    "Pending-result settings cannot be read"
                ) from error
            try:
                return PendingResultSettings.model_validate_json(content)
            except ValidationError as error:
                # An unreadable policy must never silently enable disk writes.
                raise BackendConflict(
                    "Pending-result settings are invalid; new collection admission "
                    "is unavailable until the settings are repaired"
                ) from error

    def save(self, command: PendingResultSettings) -> PendingResultSettings:
        # Validate even direct callers using model_copy/model_construct.
        command = PendingResultSettings.model_validate(command.model_dump())
        with self.coordination_lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with FileLock(self.path.with_suffix(".lock"), timeout=5):
                current = self.read()
                if command.revision != current.revision:
                    raise BackendConflict(
                        "Pending-result settings changed; reload before saving"
                    )
                saved = command.model_copy(update={"revision": current.revision + 1})
                temporary: Path | None = None
                try:
                    with NamedTemporaryFile(
                        mode="w",
                        encoding="utf-8",
                        dir=self.path.parent,
                        prefix=".pending-result-settings-",
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
