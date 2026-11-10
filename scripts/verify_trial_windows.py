"""Compatibility entry: Mac probes must use the marked native launcher."""

import sys

if __name__ == "__main__":
    if sys.platform == "darwin":
        raise SystemExit("Use the trial native launcher --trial-window-probe")
    from scopecat_app.trial_window_probe import parse_home, run

    run(parse_home(sys.argv[1:]))
