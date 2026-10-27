# Develop Scopecat

This section is for contributors changing the Scopecat repository itself.

## Set up the workspace

Development requires Python 3.14 or newer and uv:

```sh
uv sync --locked
```

Add `--group notebook` only when a local Jupyter kernel is needed. The React
project console has its own locked pnpm workspace under `apps/scopecat-ui`.

构建基线以 `.python-version` 和 `uv.lock` 为准；SDK 的 Python 3.14+ 支持
及作者环境恢复策略不因此缩窄。更新策略见 [renovate.json](../../renovate.json)，
运行时供应和原生资格化见[打包契约](architecture/desktop-packaging.md#exact-build-runtime)。
当前推荐的隔离作者构建样本与历史回归输入承担不同职责，见
[安装作者包边界](reference-fixtures.md#installed-author-package-boundary)。

Daily desktop development has one entry:

```sh
uv run --locked python -m scripts.dev
```

The root teach wrappers are retired; use desktop Help for tutorials. Pure
build/packaging commands live under `scripts.app_build`, and server maintenance
uses `python -m scopecat_server.cli`. Native installation adds no global PATH command.

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
- [Daily source development](public-preview.md#daily-source-desktop): run a checkout with native UI and HMR.
- [Release preparation and previews](public-preview.md#numbered-releases): distribute reviewed artifacts.
- [Dependency security](dependency-security.md): locked inventory audits and reviewed fixes.
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
