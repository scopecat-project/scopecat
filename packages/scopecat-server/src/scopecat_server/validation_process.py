"""Bounded cleanup of a validation launcher and the processes it owns."""

from __future__ import annotations

import logging
import subprocess
from contextlib import suppress

import psutil

_LOGGER = logging.getLogger(__name__)
_STOP_TIMEOUT = 2.0


def terminate_validation_process_tree(
    process: subprocess.Popen[str],
    *,
    owner: psutil.Process | None,
) -> tuple[psutil.Process, ...]:
    """Capture descendants before terminating the launcher that identifies them.

    Process objects retain creation times and psutil checks identity before
    signaling, so cleanup never treats a recycled PID as the owned process.
    Stop leaves first while waiting parents can still reap their children.
    The direct Popen child is always reaped through its own subprocess handle.
    """
    descendants: list[psutil.Process] = []
    owned: tuple[psutil.Process, ...] = ()
    failures: list[str] = []
    try:
        if owner is not None and owner.is_running():
            descendants = owner.children(recursive=True)
            owned = (owner, *descendants)
        identities = [(item.pid, item.create_time()) for item in owned]
        _LOGGER.error(
            "Validation cleanup owned process identities (pid, created): %s", identities
        )
        for child in reversed(descendants):
            try:
                child.terminate()
                try:
                    child.wait(timeout=_STOP_TIMEOUT)
                except psutil.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=_STOP_TIMEOUT)
            except psutil.NoSuchProcess:
                pass
            except (psutil.Error, OSError) as error:
                failures.append(f"pid={child.pid}: {error}")
    except psutil.NoSuchProcess:
        # The direct child may have completed between timeout and inspection.
        pass
    except (psutil.Error, OSError) as error:
        failures.append(f"launcher pid={process.pid}: {error}")
    finally:
        try:
            with suppress(ProcessLookupError):
                process.terminate()
            try:
                process.wait(timeout=_STOP_TIMEOUT)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=_STOP_TIMEOUT)
        except (OSError, subprocess.TimeoutExpired) as error:
            failures.append(f"launcher pid={process.pid}: {error}")
    if failures:
        raise RuntimeError(
            "Validation process cleanup incomplete: " + "; ".join(failures)
        )
    return owned
