# Develop Scopecat

This section is for contributors changing the Scopecat repository itself.

## Set up the workspace

Development requires Python 3.14 or newer and uv:

```sh
uv sync --locked
```

Add `--group notebook` only when a local Jupyter kernel is needed. The React
project console has its own locked pnpm workspace under `apps/scopecat-ui`.

Renovate 将 `pep621` 的 `uv`、规范化包名 `uv-build` 放入 `uv toolchain` 组；
`setup-uv` Action 独立更新，CI 用 `version-file: uv.lock` 读取实际 checkout 的 CLI 精确版本。
当前用户模板不声明 build backend；升级 backend 时按需核对旧教学和独立安装 fixture 的约束：
`lab_teaching/project.py`、`test_installed_adapter_journey.py`、
`fixtures/installed_author_lab/pyproject.toml`。保留兼容 fixture 的 Renovate 忽略，
不强制历史版本下限一致，也不要求 CLI 与 backend 每个 patch 同版。

## Run repository checks

```sh
uv run pytest
uv run basedpyright
uv run lint-imports
uv run ruff check .
uv run ruff format --check .
uv run --locked python scripts/check_document_links.py
uv run --locked --group docs zensical build --strict
```

Run one member's suite with its declared dependency group when iterating:

```sh
uv run --locked --package scopecat --group test pytest packages/scopecat/tests
uv run --locked --package scopecat-server --group test pytest packages/scopecat-server/tests
uv run --locked --package reference-lab --group test pytest testing/reference_lab/tests
```

## Find the relevant documentation

- [Platform status](platform-status.md): 当前能力与证据边界。
- [Architecture](architecture/index.md): product contracts and design decisions.
- [Repository map](repository-map.md): package ownership and generated code.
- [Source development and previews](public-preview.md): run and distribute a checkout.
- [Test feedback](test-feedback.md): focused checks, CI and acceptance profiles.
- [Reference fixtures](reference-fixtures.md): shared evidence and fixture ownership.
- [Workflow evaluations](workflow-evaluations.md): user goals, representative evidence and gaps.
- [Reference retirement](reference-gallery-retirement.md): retained scientific semantics and current owners.
- [Data compatibility](data-compatibility.md): persistent-format policy.
- [Project charter](project-charter.md): product scope.

Package-specific implementation details stay in package READMEs and docstrings.

## Changes and evidence

Select local checks for the behavior changed; documentation changes need link/build
checks, while a changed process or installed workflow needs its relevant integration
coverage. CI remains required. See [test feedback](test-feedback.md) for commands
and coverage limits.

Issues describe unresolved scope and acceptance; PRs record delivered changes and
validation. Update a related issue when a change affects that scope or conclusion.
Platform status links the work rather than repeating its execution history.
A source merge, released artifact, consumer upgrade and human/hardware observation
are distinct evidence. Record the version and kind of evidence actually obtained.

For an unresolved failure, retain its source/environment identity, reproduction
command, observed outcome and a sanitized diagnostic excerpt in the issue or a
durable linked artifact. A temporary path or expiring CI artifact alone is not a
portable record; identify unavailable logs without claiming fresh verification.

## Checkout outputs

[Installation layout](installation-layout.md#development-artifacts-and-retention)
distinguishes disposable build/development outputs from retained data, settings,
SDKs and authored source. After stopping development processes and saving authored
work, `git clean -ndx` previews ignored and untracked outputs; `git clean -fdx`
removes them, including local environments. Inspect the preview before cleaning.
Recreate dependencies with `uv sync --locked` and the UI's
`pnpm install --frozen-lockfile`. Device SDKs and site settings are explicit
external prerequisites, not hidden checkout inputs.
