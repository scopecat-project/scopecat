"""Detached daemon entry; install optional diagnostics before CLI imports."""

import sys

from . import _startup_diagnostics

if __name__ == "__main__":
    _startup_diagnostics.begin()
    _startup_diagnostics.stage(f"project {sys.argv[2]}")
    try:
        from .cli import app

        _startup_diagnostics.stage("CLI imported; parsing arguments")
        app()
    finally:
        _startup_diagnostics.finish()
