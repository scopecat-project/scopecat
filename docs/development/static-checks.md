# Static-check coverage and exceptions

Audit for [#1069](https://github.com/scopecat-project/scopecat/issues/1069), based on
`f6bbbfe1` (2026-10-15). Counts below describe temporary probes, not a debt score
or demonstrated runtime defects. No author API or package migration is required.

## What ordinary CI actually checks

| Gate | Command and coverage | Version source |
| --- | --- | --- |
| Python types | `uv run --locked basedpyright`: explicit root `include`, recommended mode, Python 3.14 / all platforms. Package source, core/server/instrument/quantum/reference tests, retained-signal fixture, scripts, benchmarks and testkit. This audit adds `testing/app_support` helpers and root `conftest.py`. | `uv.lock`: basedpyright 1.40.2; `.python-version` for interpreter |
| Python lint/format | `uv run --locked ruff check .` and `ruff format --check .`: repository discovery, including tests, scripts, Python fixtures and notebooks. `src` controls import classification, **not** lint inclusion. | `uv.lock`: Ruff 0.17.0 |
| Python boundaries | `uv run --locked lint-imports`: 13 contracts over configured installed packages, `scripts` and `testing.app_support`; type-checking-only imports excluded. Several contracts intentionally permit indirect imports; the top-level cycle contract uses depth 0. This is not a graph check of every test/example. | `uv.lock`: import-linter 2.15 |
| Generated Python | `uv run --locked python scripts/generate_instrument_clients.py --check`; generated clients also receive normal lint/type checks. | Locked workspace |
| UI lint/format | From `apps/scopecat-ui`: `pnpm run lint` → `oxlint .` with type-aware checking, correctness/suspicious errors, warning denial and unused-disable reporting; `format:check` → `oxfmt --check .`. | `pnpm-lock.yaml`: oxlint 1.87.0, tsgolint 7.0.2004, oxfmt 0.72.0 |
| UI types | `pnpm run typecheck` → `tsc -b --pretty false`: strict application `src`, plus node config covering Vite/Playwright config, Vite config test and e2e. `skipLibCheck` skips declaration checking, not application uses of those types. | `typescript` aliases TypeScript 6.0.2; installed `@typescript/native` does not replace this command |
| Generated UI | `pnpm run check:api` regenerates OpenAPI types/unit registry and checks their git diff. Generated schema declarations are excluded from Oxlint/Oxfmt but consumed by TypeScript. | Locked Python and pnpm environments |

CI installs pnpm from `packageManager` (12.11.2), Node from `.node-version` (24),
and Python dependencies with `--locked`. `setup-uv` reads its version from the
lockfile. Ordinary CI also runs two Python core shards, Windows/macOS smoke,
UI tests/build and strict docs/link checks; journey qualification is separate.

Ruff discovers eight member configs extending the root. Core/server/instrument/
quantum/reference/testkit configs set local import roots. Reference lab excludes
its generated `.scopecat-examples` directory. App and tutorials ignore ambiguous
Unicode punctuation for Chinese text; tutorial notebooks allow E402 because
refresh precedes author imports. `--show-settings` confirmed tutorial-local
`ignore` and `per-file-ignores` replace those root fields rather than adding to
them. Its rule selection still comes from the root. All configured Ruff path
exemptions and basedpyright source/search roots exist; cache exclusions are
intentional future paths, not stale source roots.

## Exceptions and remaining coverage boundaries

- **Global:** Ruff permits conventional builtin-like domain names and assertions.
  Basedpyright disables missing stubs, implicit string concatenation, unannotated
  class attributes and unused-call-result reporting; unused parameters and
  unreachable code are hints. These are policy choices, not evidence of defects.
  The CLI does not pass `--warnings`: restored recommended warnings are visible
  but do not alone fail CI. UI disables several effect/memo, assertion and style
  rules; this audit restores `react/hooks` after fixing a misleading callback name.
- **Directory/file:** first-match basedpyright execution environments distinguish
  tests from production. White-box tests legitimately inspect private state;
  server tests and testkit have dynamic framework/JSON boundaries. Testkit no
  longer disables ExplicitAny/UnknownMemberType/UnknownVariableType globally.
  Existing file directives still cover Arrow/NumPy or native-platform typing
  gaps. Server unused-function suppression includes a cross-module worker entry;
  the genuinely dead SQLite `_string_tuple` helper and its adapter were removed.
  Ruff TID251 exemptions allow server-owned integrations; import-linter supplies
  complementary package contracts. PLE0605 exemptions cover composed exports;
  UP046 preserves explicit covariance unavailable in PEP 695 syntax.
- **Local:** tracked `.py`/`.ts`/`.tsx` files at audit baseline contained 307 noqa
  lines, 107 file-level pyright disable directives, 90 pyright ignore lines,
  three `type: ignore` lines and five JS disable comments. These include deliberate
  invalid-input tests, optional-platform APIs, subprocess calls and incomplete
  third-party types. Ruff RUF100 and Oxlint unused-disable checks already run;
  these counts are not a target to drive to zero. No suppression was moved down
  to hide a newly enabled diagnostic.
- **Outside direct type coverage:** application tests remain a real gap
  ([#1073](https://github.com/scopecat-project/scopecat/issues/1073)). UI's Python
  OpenAPI exporter, installed-author/e2e Python fixtures and generated course
  workspaces are not direct basedpyright roots. Ruff covers actual Python/notebook
  files, not `.py.txt` templates or fenced examples. Course imports depend on
  generated workspaces and receive journey/runtime validation; lint is not a
  substitute for that validation. Basedpyright can still follow excluded files
  if imported, so exclusion is not an import boundary.

## Temporary probes and decisions

The Python probe copied the root settings into a temporary root JSON config,
added application tests/support, the exporter and conftest, removed execution-
environment overrides, and enabled unnecessary-ignore reporting. It analyzed
1,441 files in 145.7 s versus baseline 1,366 files in 145.3 s (concurrent runs;
not a controlled performance comparison). It emitted 646 errors / 5,894 warnings.
Application tests account for 94 errors / 5,035 warnings across 46 files; filling
that gap requires fixture annotations and real narrowing, not a blanket ignore.

Testkit produced 28 Any diagnostics in CLI/pytest options and JSON event logs,
and 11 private-access diagnostics in fixture construction/loader inspection.
The three removed testkit overrides produced **no** diagnostics. App-support
helpers and conftest were clean; its course resources produced 95 diagnostics
in three files, including imports resolved only in generated workspaces.
Server unused-function probing found one cross-module worker entry and one dead
SQLite helper. No unnecessary-ignore diagnostics were produced by this probe.

UI probing enabled `typescript/no-unsafe-type-assertion`,
`react/exhaustive-effect-dependencies`, `react/hooks` and `react/memo-dependencies`:
170 assertion diagnostics, six effect dependency diagnostics, one memo dependency
diagnostic and one Hooks diagnostic. The latter was an ordinary action called
`useCurrentSource`; renaming it `selectCurrentSource` restores Hooks checking
without behavior changes. Decoder validation is bounded follow-up
[#1074](https://github.com/scopecat-project/scopecat/issues/1074). Effect dependencies
need semantic review; removing ownership/source triggers merely to satisfy a
rule could change behavior. Re-enabling app Unicode rules yielded 192 diagnostics
in localized messages, supporting retention with a corrected comment.

Reproduce candidate probes with a temporary root basedpyright config and
`--outputjson`; for UI use `pnpm exec oxlint . -D <rule>` for the four rules above.
Do not commit the temporary overrides. Keep small, proven fixes separate from
broad author-type changes tracked in #581.

Targeted validation after the changes: basedpyright checked 1,372 files with no
diagnostics in 104.2 s (separate run, so no speedup claim); 13 import contracts
passed, SQLite execution tests passed (26), launch source-draft tests passed (8),
and Ruff, UI lint/typecheck/format and documentation links passed. Local UI used
the environment pnpm 11.19.0 with the frozen lockfile; CI verifies declared pnpm.
