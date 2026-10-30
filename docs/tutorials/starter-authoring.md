# 修改入门实验并分析结果 {#edit-and-analyze-a-starter-experiment}

第一次使用 Notebook，可以先从[帮助中的参数与扫描](../getting-started/quickstart.md)开始，
之后继续使用原课程目录。本页介绍的是通过设置创建的**独立作者目录**；
它使用另一套源码和 Notebook 文件，不要与课程目录混用。示例只生成合成数据，不需要设备。

## 创建目录并选择内核 {#create-folder-and-select-kernel}

1. 保持 Scopecat 桌面应用打开，进入 **设置 → 作者代码 · Author code → 新建代码目录**。
2. 点击 **选择保存位置…**，填写 **新目录名称**，再点击 **创建目录并准备 Python**。
   等待显示“作者目录已登记，请在 VS Code 中打开”。这会准备 Notebook 环境和独立的后台执行环境，
   不会发起采集。
3. 在 VS Code 中打开新目录，为 Python／Notebook 选择该目录的 `.venv`。
   Windows 对应 `.venv/Scripts/python.exe`。不要选择应用自用 Python。
4. 打开 `notebooks/02_edit_scan.py`，逐个运行单元格，从 **Imports** 和 **Connect** 开始。
   Connect 使用 `sc.notebook(...)` 找到已登记的应用，不需要复制端口号。

已有作者目录使用 **使用已有目录 → 浏览代码目录…**，填写后台的 **执行 Python** 后
**添加代码目录**。登记本身不安装依赖；没有 Notebook 环境时，另点 **创建本地 Python 环境**，
并根据依赖声明使用 **按 pyproject.toml 准备执行环境**。Notebook 内核与后台执行环境需要分别核对。

框架贡献者也可以使用[源码开发启动器](../development/public-preview.md#daily-source-desktop)。
它从空应用开始，需要先在设置中创建或登记作者目录。
独立的 `scopecat init` 起步项目仍见 [CLI 参考](../reference/cli.md#project-lifecycle)，
不是本页桌面流程的前置步骤。

## 连接前后核对什么

Connect 单元格显示作者目录、已绑定的数据目录与数据身份、作者身份，
以及当前 kernel 的 Python 和 Scopecat 版本／来源。再次执行 `print(author)` 只读
已有会话信息，不发起采集，也不重新选择应用；它不是应用在线状态检查。
这些信息不含连接令牌，但路径可能包含用户名或实验名称，公开分享前请自行隐去。
设置中的执行 Python 是后台实验环境；Notebook 摘要显示的是当前交互内核。

- **应用离线**：打开原来登记的 Scopecat 应用，再连接；不要另外启动作者目录中的数据空间。
- **目录未登记**：在目标应用的 **设置 → 作者代码 · Author code → 使用已有目录**
  登记目录并明确执行 Python，再从独立作者 kernel 连接。
- **应用重启／旧 session**：先 `author.close()`，再运行 Connect。
  从“数据”的只读代码或已保存 receipt 找回原结果，不要用 Submit 找回结果。
- **错误 kernel／版本不兼容**：选择作者环境；必要时在设置的 **查看或修改 Python 环境** 中
  **重建本地 Python 环境**，关闭旧 kernel 后重新选择。应用自用 Python 不是作者 kernel。
- **Python 下载版本不可用**：先核对下载错误。若当前下载源没有所需构建，
  可通过[应用 CLI 的 `--python` 或 `UV_PYTHON`](../how-to/maintain-application.md)
  明确选择已有兼容的独立 Python；仍会校验版本与归属，不会自动降级或借用应用解释器。
- **目标或旧 binding 冲突**：先核对原登记与 `scopecat.runtime.toml`，保留原文件和数据。
  不要删除登记记录来绕过身份校验，也不要把旧任务接到其他数据空间。

同一作者目录可以登记到多个应用。普通独立 kernel 使用已保存的默认关联；
应用启动的 kernel 继承该应用上下文。需要另一个**已登记**目标时，关闭旧会话后
显式使用 `sc.notebook(author_directory, application=application_home)`；
`application_home` 是应用的数据实例位置，不是 exe、安装目录或 Python 路径。
显式参数优先于继承上下文，继承上下文优先于保存的默认关联；离线或冲突不会自动改接其他应用。

## 修改参数并运行 {#your-editable-files}

`src/scopecat_lab/authored/signal.py` 包含合成响应、实验声明和普通分析函数。
`notebooks/02_edit_scan.py` 提供 setup、创建请求并打印运行结果链接。
这是框架用法示例，不是真实量子比特的模型或标定。

```python
params = open_parameters(author)
request = signal(center=0.0)
request.values["position"] = sc.Scan([-1.0, 0.0, 1.0])
prepared = author.prepare(request, parameters=params)
job = prepared.run()
run = job.wait(timeout=120).result()
```

先运行 **Preview** 查看准备结果，再明确运行 **Submit** 采集一次。
上面的代码展示这两个阶段；创建或编辑请求、调用 `prepare` 都不会开始采集。
修改扫描位置后，需要重新准备，再运行。`Scan` 显式指定扫描轴，普通数组不表示扫描。
`prepare` 固定本次请求，之后的修改不会改变已经准备的内容。
需要独立比较时，用 `request.copy()` 创建另一个请求。

原始三个点得到 `(0.5, 1.0, 0.5)`。脚本通过 `analyze_as` 发布
`Summary(mean=2/3, points=3)`，记录分析源码，并通过 receipt 再次打开保留的运行。
打开打印的运行链接，在 GUI 中查看同一个结果；保留运行 ID 和 receipt 路径。

`authored/parameters.py` 定义参数模型并打开 `starter` 分支。
`scale` 列实际影响响应：

```python
params["response"]["signal"]["scale"] = 2.0
params.save(note="Compare a doubled response")
```

重新准备并执行相同扫描，会得到 `(1.0, 2.0, 1.0)`。重新打开 Notebook 会沿用已保存的分支值，
不会重置为源码默认值。临时比较可以用 `params.save("trial")` 创建分支。
旧预览、运行和分析保留原输入；启动、编辑和运行不会创建全局参数默认值或人为工作点。

独立 CLI 起步项目还包含 `authored/thermometer.py`，其第一个 Notebook 使用相同的作者会话准备路径。
两份脚本都会先加载项目本地包，再刷新作者模块，因此从源码目录外运行生成脚本时，普通导入仍可使用。

## 修改源码并保留证据 {#edit-code-and-retain-evidence}

只修改请求值时需要重新准备，不需要刷新源码。修改 `authored/signal.py` 后，刷新并重新导入：

```python
author.refresh()
from scopecat_lab.authored.signal import signal
```

新增作者模块也使用这两个步骤，不需要重启 kernel 或手动 reload。
之前导入的别名和已有请求仍保留旧定义；请用新导入的声明创建新请求。
保留原运行 ID 或 receipt，通过它重新打开原结果，不要为找回结果重新采集。

`analyze_as(run.id, "scopecat_lab.authored.signal:summarize", Summary)` 默认选择该运行的原始源码。
刷新后，显式传入 `source="current"` 才会用修改后的源码重新分析。
已登记分析的选择器仍是限定名称字符串，本页不引入尚未提供的分析快捷 API。

等待超时只结束这次等待。可以再次调用 `job.wait(...)` 或重新打开 receipt；
再次调用 `prepared.run()` 会创建新的采集。

## 退出后打开同一结果 {#reopen-after-exit}

1. 等本次运行完成，记下脚本打印的 receipt 路径和运行 ID，保存源码与 Notebook 笔记。
   执行 `author.close()` 关闭作者会话；它不等于退出桌面应用。
2. 从应用 **文件 → 退出 Scopecat** 正常退出。关闭一个窗口可能只是隐藏窗口或保留后台运行，
   不应据此认定应用已经退出。
3. 再次打开原应用和作者目录，选择原 `.venv` 内核。只执行 **Imports**、**Connect**，
   按脚本注释将 `receipt` 设置为之前打印路径的 `Path`，再执行 **Reopen**。
   **不要 Run All，也不要再次执行 Submit。**
4. 核对重新打开的运行 ID、数据和分析来源。结果应属于原运行，不应为找回它新增采集。
   若提交结果原本不确定，按脚本提示使用 `author.reopen(receipt).recover()` 查询是否已接收，
   不要通过重新提交猜测结果。

## 后续阅读 {#where-to-go-next}

参数工作区和已保存计划见[托管作者会话](../how-to/managed-author-session.md)，
也可以选择其他 [Notebook 课程](teaching-sandboxes.md)。设备和量子扩展作者可继续阅读
[扩展指南](../extensions/index.md)。

## 重建本目录的客户端环境 {#rebuild-this-folders-client-environment}

通过 **设置 → 作者代码 · Author code** 准备本地环境后，将 `requirements-client.txt`
和 `author-environments/` 与源码一起保留。锁文件记录其来源快照：从选定交付复制的框架 wheels、
`client.in`（Scopecat 和 ipykernel 输入）、`delivery-constraints.txt`、
`delivery.json`（交付／源码来源）和 `python.json`（Python 版本、ABI 和二进制身份）。
`pyproject.toml` 声明额外实验依赖，但它本身不是完整客户端环境。

关闭 Notebook。在作者目录中，用 `python.json` 记录的匹配 Python 版本／ABI 创建 venv，
再安装精确客户端锁：

```sh
uv venv --python <matching-python> .venv
uv pip sync --python .venv --require-hashes requirements-client.txt
```

Windows 下会生成 `.venv/Scripts/python.exe`，重新打开 Notebook 时选择这个解释器／内核。
这些命令不会激活 shell 环境，也不会安装到应用中。不同操作系统／ABI 需要单独准备，
该锁不是跨平台或完全离线的发行物。注册表中的包需要网络或已有缓存；自定义索引和第三方本地源码
仍是明确的前置条件。保留框架 wheels 后，移动作者目录不必保留原应用安装路径。

需要改变依赖时，编辑 `pyproject.toml`，再在设置中明确重建客户端、准备执行环境。
准备过程检查选定交付的约束并保留新客户端锁；不兼容的要求会失败，不会擅自升级共享框架依赖。
若自行维护 `uv.lock`，先有意更新它：过期锁会被拒绝。临时 pip 安装不会体现在保存的锁中。
以后需要复现旧客户端时，请保留旧锁及其来源快照。

重建只影响可编辑客户端。已发布实验保留固定源码和应用管理的执行环境，
修改客户端不会替换它们的依赖或重新解释旧结果。新源码仍需通过既有应用流程准备并选择执行环境。

## 从教程换成第一次真实实验 {#from-the-lesson-to-a-first-real-experiment}

先完成合成三点扫描并保留 receipt，再和设备负责人确定下面的替换内容。
普通模板使用统一入口 `sc.notebook(...)`；登记会在源码目录外保存应用关联，
独立打开的 kernel 无需运行时文件或复制端口即可找到应用。编辑或采集前仍需核对连接身份。

| 沿用的工作流程 | 实验现场需要提供或替换的输入 |
| --- | --- |
| 请求、显式扫描、准备／预览、提交、等待、receipt 重开 | 用支持的采集／驱动实现替换合成响应函数 |
| 参数工作区、单位、已保存分支、分析发布 | 科学参数模型、单位、限值、扫描范围、时序、平均次数和标定假设 |
| 实验／分析 API 结构 | 替换 `configuration.py` 的合成 setup：真实拓扑、仪器登记、路由、能力与生命周期策略 |
| 可编辑源码与依赖声明 | 在对应客户端／执行环境中提供适配器、SDK 版本、本机驱动及现场前置条件 |

只改地址不能把合成响应变成物理测量。先与设备负责人核对 setup 和驱动合同，
验证单位与有界的小扫描输入，预览后才明确启动采集。设备退出／失败策略必须依据实际设备决定，
本教程不提供实验室通用默认策略。私有地址、凭据、SDK 和现场参数不要提交到公共源码仓库。
首次预期采集完成后，记录 receipt 并打开同一结果，不要仅为找到结果再次提交。

### 真人验收步骤（待执行） {#human-acceptance}

先用上述无设备示例观察一位目标用户的完整操作，再决定真实实验场景；本页整理不代表验收已经完成。

1. 记录应用版本、Python／ABI 和选定环境锁。由用户创建独立作者目录、在 VS Code 选内核并核对连接身份。
2. 按页内步骤运行原始三点扫描，再改一个参数重新准备并运行；确认两次结果不同且原结果仍可打开。
3. 保存源码、receipt 和运行 ID，正常退出再重开，只读找回原结果及来源，确认没有重复采集。
4. 记录哪些步骤需要帮助、实际按钮或报错、具体阻塞及未完成项。身份不符或环境准备失败时停止采集，
   先核对配置；不要覆盖现有安装、迁移日常数据或用真实设备绕过合成示例的问题。

完成条件是目标用户走通创建、编辑、运行、退出与重开，并能辨认原结果和新结果。
自动化 Linux 示例／内核检查和维护者已有合成验收不能替代这项真人观察，也不能证明真实设备兼容。
