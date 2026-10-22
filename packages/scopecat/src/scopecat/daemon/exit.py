"""Application exit report; deferred records remain durable and unfinished."""

from pydantic import BaseModel, ConfigDict


class ExitWork(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: str
    identity: str
    reason: str


class ExitReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    draining: bool
    finishing: tuple[ExitWork, ...] = ()
    deferred: tuple[ExitWork, ...] = ()
    blockers: tuple[ExitWork, ...] = ()

    @property
    def ready(self) -> bool:
        return self.draining and not self.finishing and not self.blockers
