# Scopecat Server

The installed application console `scopecat` belongs to `scopecat-app`.
Server-only development uses `python -m scopecat_server.cli` for project commands;
the server never imports the application host or delivery tools.

本包提供本地 FastAPI、SSE 传输和默认 SQLite daemon runtime。标准 server wheel
不内嵌 GUI；匹配 GUI 是单独 ZIP，维护者可用 `--static-dir` 指向其解压目录。
源码调试也可使用 Vite 或已构建的 `apps/scopecat-ui/dist`。
这些是 server 维护与集成入口；普通桌面安装和首次实验见
[快速开始](../../docs/getting-started/quickstart.md)。

在仓库根目录，选择尚不存在的临时目录，验证当前生成器与 bootstrap：

```console
uv run --locked python -m scopecat_server.cli init /tmp/scopecat-server-example
uv run --locked python -m scopecat_server.cli config check /tmp/scopecat-server-example
```

Windows 可将示例路径换成自己的临时目录。`init` 不覆盖已有文件；`config check`
验证初始 setup 和可选 parameter defaults，不启动 daemon 或创建项目运行状态。
当前生成器使用 setup 与 configuration templates，预期输出
`valid setup=… parameter_defaults=none`。模板内容不由此命令校验。
命令与当前配置操作见 [CLI 参考](../../docs/reference/cli.md#configuration)；
原 `config diff/apply/export` 已退役。

生成的 `scopecat.toml` 分开声明轻量 bootstrap、作者模块和 instrument backend：

```toml
[lab]
bootstrap = "scopecat_lab.application:create_bootstrap"
instrument_backend = "scopecat_lab.backend:create_backend"

[authors]
modules = ["scopecat_lab.authored"]
dependencies = ["scopecat-instruments"]
source_roots = ["src"]
```

需要自定义系统时可另加 `[lab.capabilities]`，例如
`experiment_system = "my_lab.system:build_experiment_system"`。

The system builder is optional: the generated starter uses the default system.
When supplied, it accepts the accepted configuration and instrument contract
catalog as positional arguments and returns an `ExperimentSystem`. Other optional
capabilities declare procedure and schedule symbols, calibration registries,
publication policies and launch/comparison providers. These are existing values
or callables, not project-root factories.

下面是生成的 `src/scopecat_lab/application.py` 的精简示例，使用当前 `LabBootstrap` 字段：

```python
from pathlib import Path

from scopecat.application import LabBootstrap
from scopecat.records.configuration_template import ConfigurationTemplate

from .authored.parameters import initial_parameters
from .configuration import initial_setup


def create_bootstrap(_project_root: Path) -> LabBootstrap:
    return LabBootstrap(
        setup=initial_setup,
        configuration_templates=configuration_templates,
    )


def configuration_templates() -> tuple[ConfigurationTemplate, ...]:
    parameters = initial_parameters()
    return (
        ConfigurationTemplate(
            id="starter-software",
            label="软件实验台",
            setup=initial_setup(),
            catalog=parameters.parameter_catalog,
            parameters=parameters.parameter_snapshot,
        ),
    )
```

`setup` 在首次使用时创建名为 `initial` 的设备 setup；可选 `parameter_defaults`
返回独立的 `ParameterRevisionContent`，并要求同时声明 setup。已初始化的存储重启
不会重新求值这两个初始工厂。`configuration_templates` 返回供显式导入的模板，
与初始参数默认值分别声明；当前生成器不设置 `parameter_defaults`。

Bootstrap 与 backend 工厂接收解析后的项目 `Path`。上例依赖 `init` 生成的其他模块；
backend 实现在生成的 `scopecat_lab/backend.py`，使用软件 instrument provider。
能力符号在 Notebook 或项目 worker 中解析；daemon 只加载轻量 bootstrap。
自定义 `lab.application` 工厂仍用于特殊组合，不能与 `[lab.capabilities]` 同用。
Notebook planning receives the accepted snapshot and the
daemon-resolved contract catalog. Backend code, transports, codecs, and drivers
are imported and constructed only in the long-lived instrument worker.

同一项目只由一个 daemon 持有。上述生成项目在没有自定义 runtime binding 时，
将 SQLite 和不可变对象存入 `.scopecat/`，回环端点记入 `.scopecat/daemon.json`，
并为 GUI 与 Notebook 客户端提供可重放事件流。新桌面应用的数据与状态目录另见
[安装布局](../../docs/development/installation-layout.md#installed-application-directories)。See the
[daemon model](../../docs/development/architecture/daemon.md) for ownership, fencing, quarantine,
and security boundaries.

## Project-store compatibility

This early implementation uses an explicitly versioned project store and does
not perform implicit migrations. The current version is defined by
`PROJECT_SCHEMA_VERSION` in `scopecat_server.storage.sqlite.schema`. A daemon
built from this revision refuses an older `.scopecat/control.sqlite3` instead
of partially reading or rewriting it.
Before switching revisions, stop the daemon and back up the complete
`.scopecat/` directory. If stored runs or configuration history matter, inspect
or export them with the revision that created the store. Rebuilding means
explicitly starting from an empty runtime store and accepting that the daemon
will seed a new registry from `create_bootstrap`; source-controlled project
files are not a substitute for persisted run history.

The daemon never deletes an incompatible store automatically. This rebuild-only
policy is intentional for the current closed development phase; a supported
migration or export/import boundary is required before project stores are
treated as long-lived user data.

The minimal installed-framework check consumes standard public artifacts but only
installs core/server/instruments/quantum and their dependencies. It excludes the
application host, teaching package and testkit. The matching GUI is a separate ZIP,
served with `--static-dir`; standard server wheels do not embed it. This internal
dependency check is not a separate desktop product.

Commands use the environment's `python -m scopecat_server.cli`; `scopecat app`,
`notebook`, `teach` and tutorial `init --topic` belong to an installation containing
`scopecat-app`. Previous server-only callers of `scopecat COMMAND` must change
to the module entry. Complete application installations keep `scopecat COMMAND`.

Build and verify artifacts from the current committed source:

```console
uv run --locked python scripts/build_preview.py dist/framework
uv run --locked python scripts/verify_installed_framework.py dist/framework
```

Tests may construct `LocalDaemonRuntime` with a temporary project or pass a
custom `DaemonApplication` to `create_app`.
