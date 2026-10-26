# Array calibration tests

This directory owns the shared array-calibration recovery and publication scenario.
It groups a behavior, not a runtime tier. Course-kernel and lesson-material tests
remain with their current owners until there is a concrete shared boundary to move.

- `test_calibration_smoke.py` keeps the two-target healthy chain in `integration`
  and therefore `core`.
- `test_array_maintenance.py` qualifies six targets and fault/recovery cases in
  `journey`.
- `support.py` owns the common runner and subprocess scenario. Neither test imports
  another test module. The generated author fixture remains in
  `../fixtures/array_maintenance.py`.

Both entry points use real daemon/worker processes and temporary project storage.
They inherit the existing root application-locator isolation; no directory-local
fixture overrides are needed. The assertions, scenario timeouts and case IDs are
shared unchanged. Failure nodeids identify the entry point and qualification case.

Run the affected entry point from the repository root:

```sh
uv run --locked pytest packages/scopecat-app/tests/calibration/test_calibration_smoke.py -n 0 -q
uv run --locked pytest 'packages/scopecat-app/tests/calibration/test_array_maintenance.py::test_array_maintenance[readout-failure]' -n 0 -q
```

For a new test, choose the owning behavior first, state its real and replaced
boundaries, and classify its execution profile explicitly in `pyproject.toml`.
Inspect selection with `python -m scopecat_testkit.check <suite> --list`.
Runtime weights estimate cost; they do not define responsibility or platform.
Keep shared fixtures at the nearest common ancestor. Split a mixed file only when
its environments, fixture lifetimes or execution cadence need independent control,
not to make every file fit a pure unit/integration label.
