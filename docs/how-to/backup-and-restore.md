# 备份与恢复已停止的应用或项目 {#back-up-and-restore-a-stopped-application-or-project}

日常原生应用备份可使用[设置中的备份入口](maintain-application.md#back-up-application-data)。
本页说明维护者的显式命令流程，只支持当前运行时的开发数据格式和**快照格式 1**。
快照清单记录精确 schema 版本，校验需要匹配的运行时。
这里的快照格式编号不代表桌面交付包或作者环境资源的格式编号。
快照恢复不是升级路径，也不是兼容性基线；另行支持的升级边以
[数据兼容策略](../development/data-compatibility.md)为准。

快照保留 SQLite 数据库、不可变测量与分析对象、应用/配置源码，
以及已安装 Python 和包的版本记录。它是可用普通备份工具复制到其他磁盘或归档的目录。
请复制整个快照，包括 `manifest.json` 和 `project/`；版本记录不等于环境文件备份。

备份前停止 daemon 及另行启动的 procedure 或自动化 worker，并保存、停止编辑项目源码：

```sh
python -m scopecat_server.cli stop my-lab
python -m scopecat_server.cli snapshot create my-lab backups/my-lab-2026-09-08
python -m scopecat_server.cli snapshot verify backups/my-lab-2026-09-08
python -m scopecat_server.cli snapshot restore backups/my-lab-2026-09-08 recovered-lab
```

快照的父目录必须已存在。创建目标必须是源项目之外的**新目录**；
恢复必须使用**新的项目路径**，绝不替换已有项目。
创建期间始终持有 daemon 进程锁和 SQLite 写入预留锁；存在运行中的 daemon
或活动 SQLite 写入者时会拒绝操作。这是停机备份，不是在线备份，
也无法协调并行编辑源码的工具或绕过 Scopecat 占用契约的第三方写入者。

## 应用主目录 {#application-homes}

同一套停机快照命令也接受包含 `scopecat.application.json` 的显式应用主目录，
不需要空的作者项目。已识别的旧桌面布局应选择其 `runtime` 目录。

应用快照保留 `data/` 科学记录、不可变源码包、原始作者作业回执和练习文件，
以及 `state/author-workspaces.json` 和部署身份。
不包含安装的应用、外部可编辑作者目录、环境内容、缓存、活动端点和派发意图。
登记中的解释器路径仍只是引用；环境缺失不妨碍读取恢复的数据。
外部代码、SDK 和环境构件需单独保留。

显式恢复按当前布局创建全新应用主目录，保留科学身份和引用，
只更新登记中的服务位置，不改写外部作者绑定，也不启动工作。
使用当前应用包准备该目录；执行前，显式登记恢复的作者目录，沿用已有工作区身份和保留的环境。
复制的作者目录不应携带原机器的运行绑定；恢复不会改写已有外部绑定。
恢复副本不是可独立写入的克隆，接管前必须停止原所有者。

## 保留与排除的内容 {#what-is-retained}

普通项目快照保留：

- `.scopecat/control.sqlite3`，包括保留的 SQLite WAL 中已提交的数据，合并捕获为独立数据库。
- 所有不可变对象，并验证数据库对每个对象的引用。
- 项目内源码和数据文件，包括 `scopecat.toml`、`src/`、`config/`、Notebook、依赖声明与锁文件。
- 清单中的快照格式版本、schema 版本、捕获时间、Python 版本、已安装发行包名称/版本及
  SHA-256 文件清单。恢复还会将清单保存为 `.scopecat/restore-manifest.json`。

运行端点/PID 记录、锁、日志、未发布临时对象及 GUI procedure 派发状态不在快照中。
源码目录各层级的 Git 内部文件、`.venv`、`venv`、`node_modules`、`__pycache__`、
`.pytest_cache` 和 `.ruff_cache` 都会排除。遇到符号链接会拒绝，而不是跟随到项目外。
其他位置的文件，包括单独安装的 SDK、editable 包、驱动安装及硬件设置，不会归档。
请单独保留原始包构件和所需外部 SDK 的安装程序/源码；仅有名称和版本无法重建
editable 检出目录或已无法取得的专有依赖。

这些命令不导入或执行项目应用/配置代码。
校验检查文件清单与校验和、SQLite 完整性和外键、当前 schema 版本以及对象内容哈希。
引用的内容缺失或损坏会导致创建或校验失败。
校验和能发现损坏，但不能认证未知快照发布者；决定运行恢复源码前，应将其视为可执行代码审查。

## 启动恢复的项目 {#starting-a-restored-project}

根据记录的 Python/包版本和保留的构件重建环境，安装外部依赖，
并针对目标机器检查恢复的配置，再显式启动 daemon。
快照恢复不会安装包、启动 daemon、导入应用或占用硬件。

读取已有结果无需重新连接作者源码。
若要沿用现有源码身份追加分析，启动服务前，应在服务停止时显式登记恢复后的位置：

```sh
scopecat register-workspace recovered-lab --service recovered-lab --identity EXISTING_WORKSPACE_ID
```

登记契约见[源码身份与搬迁](../reference/project-layout.md)。
本地源码路径和 Python 环境不会作为部署绑定恢复。

持久 procedure 记录（包括就绪和未完成工作）会保留。
旧 GUI 的管理文件被排除，启动恢复后的 GUI 不会自动继续这些 procedure；
准备好后应显式派发所选工作。显式启动常驻自动化 worker 仍可能正常派发符合条件的工作。
daemon 启动时仍应用既有的中断运行和租约恢复规则，恢复不会改写其历史。

## 开发数据格式变化 {#development-format-changes}

快照工具拒绝与当前版本不匹配的格式，拒绝不会升级、删除或改写原数据库及其 SQLite 辅助文件。
运行时对不支持的格式也会拒绝启动；单独支持的显式升级（目前为 115→116）见
[数据兼容策略](../development/data-compatibility.md)，不能用快照恢复替代升级。
开发 schema 编号不承诺后续版本可读。过去 schema 68–74 的迁移演练及迁移 CLI 已退役。

若需历史归档阅读，请单独保留旧存储和原环境。
除明确支持的操作外，新构建不为兼容基线之前的旧格式提供读取、恢复或迁移承诺。
新开发状态应使用新位置，不要为了让新运行时启动而删除旧数据库。

在当前格式内，快照保留作者源码包、工作区范围内的 publication 状态、
准备回执、作者作业回执和实验计划。待完成的准备在重启或恢复后变为中断，
不会自动用今天的文件重新提交。外部 Python 环境和设备 SDK 仍需另行保留其构件。

## 数据位于独立目录时 {#separately-located-data}

工作区可通过 `scopecat.runtime.toml` 选择本地存储和一个执行部署，见
[项目布局](../reference/project-layout.md)。快照锁定并读取该数据根目录，
包含作者作业回执，并恢复到新的同目录 `.scopecat/`。
不会复制本地运行绑定、端点、部署占用或自动 procedure 派发意图。
数据空间身份与科学引用在恢复后保留。
接管恢复副本前停止原服务；尚不支持可独立写入副本之间的可移植合并。
