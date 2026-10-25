# 维护应用 {#maintain-the-application}

Scopecat 原生入口直接打开工作台，没有实验室选择器或单独的管理页。
**设置（Settings / Application settings）**管理软件、作者目录和数据位置；
**设备与驱动（Devices and drivers）**管理共享设备。下文括号内及英文粗体文字对应当前界面入口。

## 日常使用 {#daily-use}

若只需查看或分享已保存的结果，请从[打开与分享已有数据](open-and-share-data.md)开始，无需作者目录。

1. 打开 Scopecat，再在 VS Code 中打开实验代码目录。
2. 为脚本和 Notebook 内核选择已准备好或自行管理的 Python，在当前任务中选择代码、实验配置和参数。
3. 保存编辑并刷新作者代码。这会捕获源码，不会重建应用或安装依赖。
4. Mac 和 Windows 上，菜单栏或系统托盘可用时，关闭最后一个窗口会让 Scopecat 继续在后台运行，
   托盘菜单提供**打开 Scopecat**和**退出 Scopecat**；关闭额外窗口只关闭该视图。
   托盘不可用或尚未确认时，最后一个窗口保持打开，可最小化，或使用**文件 → 退出 Scopecat**。
   选择**退出 Scopecat**时，空闲应用直接退出；有未完成工作时，可等待、停止工作并退出，或继续后台运行。
   仅有空闲 Python 会话不会阻止退出。

重新打开应用只恢复对已保存记录的访问，不会重复测量。
关闭浏览器标签页或 Python 客户端不会停止应用。

## 备份应用数据 {#back-up-application-data}

在原生应用中选择**设置（Settings）→ Software and data → Back up application data…**。
选择应用主目录之外的现有文件夹，保存并停止编辑练习文件，然后确认。
取消任一对话框都不会改变数据。应用仅在空闲时停止服务；活动工作和文件传输必须先结束。

结果页显示唯一命名的快照目录及校验结果。请保留整个目录。
服务会保持停止；选择**重新打开应用**继续，或选择**退出**保持停止。
失败时原数据和环境保留，修正原因后可重新打开并重试。
未完成或未通过校验的目录不能作为可用备份。

此入口使用当前格式快照，保留科学记录、不可变源码包、练习文件和作者目录登记。
**不包含**外部可编辑作者目录、Python 环境、SDK 或应用安装包，请另行保留。
备份不会迁移数据，也不是完整环境归档。能否恢复取决于快照与数据格式兼容契约，
不能只看应用版本号。恢复仍由维护者显式执行到**新的应用主目录**，见
[备份与恢复](backup-and-restore.md)；没有原地恢复按钮。
这个原生备份入口不要求、也不会安装全局 `scopecat` 命令。

## 安装新版应用 {#install-a-newer-application}

退出 Scopecat，安装对应平台的新版原生安装包，再重新打开。
安装包已包含 Python 和应用依赖，启动直接运行随包版本，不会另外安装执行环境。
使用应用无需系统 Python、uv 或在设置中选择应用环境。

启动期间窗口会先出现；失败时在同一窗口提供重试和退出入口。
如果旧服务仍在运行，请先完成其工作再重试，或显式停止旧服务以完成更新。
启动不会悄悄替换成旧桌面版本。应用更新保留用户 Python 环境及已保存任务使用的环境。
客户端包更新是独立操作，重建前应先关闭内核。

## 作者目录 {#author-folders}

首次 Notebook 实验请按[帮助页课程](../getting-started/quickstart.md)开始。
课程目录已登记，可直接成为日常编写实验的目录。
若需独立目录，在**设置（Settings）→ Author code → New code folder** 中选择保存位置和名称，
然后点击 **Create folder and prepare Python**。应用创建无需设备的示例和独立 Python 环境，
不会连接设备或开始测量，也不会覆盖现有目录。

准备完成后，设置页显示目录和 Python 路径。在 VS Code 中打开该目录，
选择 `.venv` 解释器，逐单元格运行 `notebooks/02_edit_scan.py`。
示例会提交合成扫描、分析并重新打开已保存结果；再次运行提交部分会产生一次新测量。

接入已有 Scopecat 源码时，选择 **Use existing folder → Browse for code folder…**，
填写 **Execution Python**，再点击 **Add code folder**。
可在 **Your code folders** 中选择已登记目录。添加目录不会停止应用；
多个目录共享同一服务、设备登记和数据管理。

对已登记目录，`sc.notebook()` 可使用用户选择的兼容内核，同时保留旧 `.venv`。
导入前仍会检查所选源码的 Scopecat API 版本与已安装作者包内容，登记不能跳过这些检查。
Notebook 内核与执行 worker 不必使用同一个解释器路径。
请保留旧环境，以便执行已保存源码及恢复工作。

如果现有目录没有本地 Python，选择 **Create local Python environment**，
随后选择 macOS 的 `.venv/bin/python` 或 Windows 的 `.venv/Scripts/python.exe`。
这是用户环境，在其中安装绘图或分析包不会修改应用。
不要把应用安装目录内的解释器作为 Notebook 内核。
生成的环境使用由 uv 管理的独立 Python；环境使用期间请保留它。
更换或卸载应用不会删除这个 Python。

首次准备需要联网下载 Python 和第三方依赖；应用本身仍可离线启动。
作者资源只携带所需的未发布 Scopecat wheels 和匹配的依赖配方，不是完整离线依赖包。
目录内的 `.python-version` 优先于配方固定的 Python 版本，并会检查 `requires-python`
和声明的依赖。已有 `uv.lock` 用 `--locked` 导出；过期锁文件会导致失败，不会被重写。
应用不会选择 `latest`、修改 PATH 或安装全局包。

应用 CLI 的准备操作可通过 `--python /path/to/python` 使用已有独立解释器；
设置了 `UV_PYTHON` 时也会检查它。选择已准备好的后台环境时，
**Execution Python** 会先验证环境。准备失败保留原环境。

生成的环境包含 Scopecat Python API、Notebook 内核及其依赖，不复制桌面、服务和教学包。
VS Code 可直接使用。如需 JupyterLab 编辑器，用该环境的 pip 安装 `jupyterlab`；
维护者的 `scopecat notebook` 命令也使用该目录的 `.venv`，不会使用应用 Python。

如果后台实验也需要某个包，请在作者目录的 `pyproject.toml` 中声明，例如：

```toml
[project]
name = "my-experiments"
version = "0.1.0"
dependencies = ["humanize==4.13.0"]
```

选择 **Prepare execution environment from pyproject.toml**，然后刷新并预览实验。
准备操作在独立受管环境中解析源码需求与兼容的 Scopecat 包。
交付包提供的依赖仍受完整依赖锁约束，与本地 Notebook 环境使用相同基线；
联网解析可以添加声明的包，但不能静默升级该基线。
依赖冲突会失败，不会修改运行中的应用或所选源码环境。
已准备的工作和计划保留其记录的依赖版本，应用保持运行。
仅在本地 pip 安装不会改变后台执行环境。

客户端导入会检查源码声明的依赖范围和匹配的 Scopecat API 版本，
不要求每个分析库版本或 Python 补丁版本都与执行环境相同。
已保存工作的执行和恢复仍检查精确记录的环境；原地修改用户环境可能使先前准备的工作失效。

本地环境损坏时，先关闭其终端和内核，再选择 **Rebuild local Python environment**。
旧目录保留为 `.venv-retained-…`，重建失败会恢复旧目录。
新环境基于所选交付包创建，之后需重新安装自行添加的包。
此操作不会修改源码、测量记录或受管执行环境。

帮助页课程导入失败时，关闭该课程的 Notebook 和 Python 终端，再展开
**Repair Notebook environments**，点击 **Repair environments and open Notebook**。
修复保留同一目录、源码编辑、Notebook 笔记和已保存结果，保留旧本地环境，
并按交付依赖锁准备后台环境；之前准备的工作仍使用其记录的环境。
重新启动 Notebook 时选择页面显示的 `.venv` 内核，并按需重装自行添加的包。
多个帮助窗口观察的是同一次准备操作：运行中请等待，失败或中断后重试仍使用同一目录。

受管执行环境位于 `HOME/environments`，已保留的环境不是可丢弃缓存。
也可以填写现有 Python 并选择 **Use this execution Python**，不会安装包。
驱动更新默认使用登记源码的执行环境，除非在驱动面板显式覆盖。
修改源码执行 Python 不会替换已连接的驱动。

维护者可通过命令执行同类操作，无需打开浏览器：

```sh
scopecat app --home /path/to/Scopecat --action register-source --workspace /path/to/code
scopecat app --home /path/to/Scopecat --action start
scopecat app --home /path/to/Scopecat --action status
scopecat app --home /path/to/Scopecat --action stop
```

仅 `--action open` 打开浏览器；`--action desktop` 打开原生窗口。
源码检出目录在构建 GUI 后，可先用 `--action configure --source /path/to/scopecat`
配置独立开发应用目录。已安装的能力包属于源码所选的执行环境，
其 manifest 应声明在该源码的 `scopecat.toml`，见
[已安装的实验室适配器](../development/architecture/public-application.md#installed-laboratory-adapters)。
不要将其安装到应用环境或配置在应用本身。

## 在 VS Code 中开发能力 {#develop-a-capability-in-vs-code}

使用独立的前台开发应用，在 VS Code 中编辑普通源码：

```sh
uv run --locked python -m scripts.dev --home /path/to/development
```

在公库检出目录运行，需 Python 3.14+、uv、Node.js 和 pnpm。
命令打开带 Vite 和独立后端的原生桌面；Ctrl-C 请求在工作空闲后退出。
通过设置登记能力源码目录，详见[日常源码开发](../development/public-preview.md#daily-source-desktop)。
登记源码不会启用驱动。实验和分析修改使用普通作者代码刷新；
首次选择或修改驱动使用**设备与驱动（Devices and drivers）**中的 **Update from source**，
此前应结束活动工作并释放手动会话。仅保存文件不会替换正在使用的驱动。
依赖变更需要显式准备开发环境。应用更新使用新构建的原生包，
不在运行中的应用内切换候选解释器或能力 wheel。

## 恢复运行 {#recovery}

启动发现其他解释器时，使用桌面的停止后台并重启操作，或执行上面的显式停止命令。
停止依据已记录的进程身份，即使旧解释器或可选适配器已不可用，也不会按名称杀死无关进程。

强制终止后，系统会在运行锁保护下核对有效但过期的占用记录。
锁文件存在本身不代表进程存活；无法确定归属时仍报错，不会删除记录或猜测 PID。

修改本地设置后，在设置页选择 **Stop work and restart**。
重启会先检查当前应用和设置；失败时窗口保留错误及重试入口。
应用依赖损坏应安装修正后的应用包，不要在应用内使用 pip。
**Technical diagnostics** 提供维护者需要的解释器和能力详情。

原生日志位于 `HOME/desktop/desktop.log`。新建应用的科学数据位于 `HOME/data/`，
daemon 日志位于 `HOME/state/daemon.log`；`state/` 还保存安装登记、作者目录登记、
解释器引用和运行协调信息，**不是可丢弃的缓存**。已识别的旧布局（legacy）保留
`HOME/runtime/.scopecat/` 下原有的数据和 daemon 日志，不会自动搬迁。
记录准确错误和应用路径；历史目录不会自动删除。完整边界见
[安装布局](../development/installation-layout.md#installed-application-directories)。

## 占用规则与限制 {#ownership-and-limits}

开发应用目录独立于日常应用目录。测试使用临时目录，不要借用日常服务。
不同应用主目录之间不会协调对同一真实设备的访问。

应用保留 daemon/worker 隔离及关闭检查。更新是显式中断，不授予恢复采集的权限。
持久数据兼容性只从明确指定的基线开始承诺，目前尚未指定该基线。
见[数据兼容策略](../development/data-compatibility.md)和[备份与恢复](backup-and-restore.md)。
帮助页练习使用同一个应用，数据清理也使用普通数据清理入口。

## 不支持的数据格式导致启动失败时重置数据 {#reset-data-when-an-unsupported-format-prevents-startup}

原生错误页提供**删除应用数据并重新初始化…**。
默认先选择外部备份文件夹，再确认精确的数据范围；应用只记住这个文件夹。
每次恢复都默认启用备份，**本次跳过备份**只对本次确认生效。
取消任一对话框都不会删除数据，备份失败绝不会转为直接删除。

原件备份逐字节保留数据库、WAL 和对象文件，不读取或归档可重建的
`control.sqlite3-shm` 索引；清单会注明这一项。该索引不含数据库内容，
SQLite 可从数据库和 WAL 重建它。备份期间仍持有写入保护，不执行 checkpoint。
参见 [SQLite WAL 文件说明](https://www.sqlite.org/walformat.html#the_wal_index_or_shm_file)。

操作只删除确认的数据目录中的 `control.sqlite3`、其 SQLite sidecar 文件和 `objects/`，
再在原路径初始化当前格式的空存储。新布局的数据目录为 `HOME/data/`；已识别的
legacy 布局仍为 `HOME/runtime/.scopecat/`。不会删除整个数据目录或应用 home。
这会移除实验、参数、草稿、设备配置和已存证据；原始作者目录、Python 环境、
应用文件、作者目录登记及其他文件保留。新布局的 `state/` 也保留，不属于科学数据重置范围。
不会自动运行任务或连接设备，也不会清理环境、物化源码或未知辅助文件。
此操作不创建新数据空间或空间管理入口。

备份在所选文件夹中使用唯一目录名，包含原始存储文件和 SHA-256 清单。
它保留原格式，**不承诺能按当前版本的快照流程恢复**，也不包含源码或 Python 环境。
支持格式的日常备份使用上面的设置入口；显式恢复仍是维护者的独立命令。
所有归档文件均在删除前复制并校验。删除中断可能留下部分删除状态，无法自动回滚。
重试仍需显式确认；已有已验证归档会被复用并显示位置。
若上次跳过备份，新备份只能包含剩余数据。

恢复会拒绝自定义运行绑定或组合、不确定或活动中的所有者、重定向路径、
对象存储中的未知文件及未解决的 rollback journal。
出现这些错误应保留数据并核查，不能猜测处理。
