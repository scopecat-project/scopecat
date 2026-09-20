"""Expected launch checks retained across worker, HTTP and notebook boundaries."""

from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from scopecat.kernel.problems import ModelLocation, Problem
from scopecat.records.execution_scenario import SoftwareExecutionScenario


class LaunchRejection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Literal["launch_rejection"] = "launch_rejection"
    message: str
    problems: tuple[Problem, ...] = ()
    scenario: SoftwareExecutionScenario | None = None


class AuthorLaunchRejected(ValueError):
    """An expected launch rejection; diagnostic retains structured findings."""

    def __init__(self, diagnostic: LaunchRejection) -> None:
        self.diagnostic = diagnostic
        lines = [diagnostic.message]
        for item in diagnostic.problems:
            line = f"{item.code}: {item.message}"
            if isinstance(item.location, ModelLocation):
                location = ".".join(
                    (item.location.root, *(str(part) for part in item.location.path))
                )
                line += f" [at {location}]"
            elif item.location is not None:
                fields = cast(
                    "dict[str, object]", item.location.model_dump(exclude_none=True)
                )
                location = ", ".join(
                    f"{key}={value}" for key, value in fields.items() if key != "kind"
                )
                line += f" [at {location}]"
            lines.append(line)
        if diagnostic.scenario is not None:
            scenario = diagnostic.scenario
            lines.append(
                f"Scenario: {scenario.label} [{scenario.id}] "
                f"({scenario.model_id} {scenario.model_version})"
            )
            lines.extend(f"Limit: {limit}" for limit in scenario.limitations)
        super().__init__("\n".join(lines))


class LaunchRejectionResponse(BaseModel):
    """HTTP detail also permits existing textual and request-validation failures."""

    detail: LaunchRejection | str | list[dict[str, JsonValue]]
