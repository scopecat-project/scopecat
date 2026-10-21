"""Explicit application context for child author processes."""

import os
from pathlib import Path


def author_worker_environment(application_root: Path | None) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("SCOPECAT_DAEMON_URL", None)
    environment.pop("SCOPECAT_APPLICATION_ROOT", None)
    if application_root is not None:
        environment["SCOPECAT_APPLICATION_ROOT"] = str(application_root)
    return environment
