"""Declared scope of durable work at the application exit boundary."""

from typing import Literal

type ExitScope = Literal["current", "subsequent", "maintenance"]
