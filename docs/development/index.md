# Develop Scopecat

This section is for contributors changing the Scopecat repository itself.

## Set up the workspace

Development requires Python 3.14 or newer and uv:

```sh
uv sync --locked
```

Add `--group notebook` only when a local Jupyter kernel is needed. The React
project console has its own locked pnpm workspace under `apps/scopecat-ui`.

## uv toolchain 维护

Renovate 将 `pep621` 提取的 `uv` 和规范化包名 `uv-build` 放入同一
`uv toolchain` 组；`astral-sh/setup-uv` Action 独立更新。CI 的
`version-file: uv.lock` 从实际 checkout（包括 reusable workflow 的目标 ref）
读取 CLI 精确版本。分组用于一起审查，不要求 CLI 与 backend 每个 patch 同版。
保留 lab-tools 的 `uv` 运行依赖、delivery 的 `uv_build` 和独立的
`delivery-build` 构建环境声明。

审查 backend 升级时，核对以下维护点，避免只更新 `pyproject.toml` 后遗漏嵌入文本：

- 当前 `scopecat init --topic` 调用 `lab_tools.notebook_journey.create_lesson_source`，
  不声明 build backend；无 topic 的 server scaffold 也不生成 backend 声明。
- `lab_teaching/project.py` 的 `PYPROJECT` 是教学回归 fixture；
  `test_installed_adapter_journey.py` 的内嵌项目直接调用已安装的 backend 构建 wheel。
  两者不经 `pep621` 自动提取，升级时人工确认其约束是否仍适用。
- `fixtures/installed_author_lab/pyproject.toml` 是独立安装兼容 fixture，继续保留
  Renovate 忽略；仅在兼容契约确实改变时调整，不机械同步所有历史版本下限。

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

- [Platform status](platform-status.md): current capability boundaries and open work.
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
