---
default: patch
---

Allow calibration Notebooks to submit follow-up requests without reloading imports.

Calibration and joint-calibration lessons can reconnect and submit again without
errors caused by replaced Python classes. Requests still use the current source.
