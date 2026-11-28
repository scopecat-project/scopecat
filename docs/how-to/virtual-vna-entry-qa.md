# 隔离验证 Virtual VNA 上手入口

本检查只使用模拟设备、独立应用目录和作者目录。它不验证物理接线、校准或真实设备，
也不替代 Windows/macOS 安装包与原生 WebView 的人工验收。不要使用日常应用数据目录。

## 固定候选与运行入口

在新 clone 中检出交付记录中的完整 commit SHA，再安装该 commit 锁定的依赖：

```sh
git checkout --detach <完整候选SHA>
uv sync --locked
cd apps/scopecat-ui
pnpm install --frozen-lockfile
pnpm run build
pnpm exec playwright install chromium
```

运行下列测试，会创建空白标准应用，实际通过 UI 启用模拟来源、登记 Virtual VNA、
复制实验声明，再用 GUI 和 Python 分别准备及运行。作者文件和参数分支通过公开 API
创建；没有预置设备或命名 setup。保留的应用目录通过 `isolated-home` 测试附件报告。
`SCOPECAT_QA_ROOT` 可指定已存在的专用临时父目录，默认使用系统临时目录。

```sh
pnpm exec playwright test e2e/vna-entry.e2e.ts --project journey-2 --workers 1
```

测试保留目录、`evidence.json`、剪贴板原文和 Python receipt，以便复核。
截图及失败 trace 在 Playwright 输出目录；重跑时如需保留旧输出，请另传 `--output`。
它使用真实浏览器与 daemon，但不是原生 WebView 验收。

## macOS / Windows 原生人工检查

从仓库根目录，以全新绝对路径代替 `<QA_HOME>`，以构建输出绝对路径代替 `<DIST>`。
无需更新日常安装，也不运行迁移或清理命令：

```sh
uv run --locked scopecat app --action configure --home <QA_HOME> --static-dir <DIST>
uv run --locked scopecat app --action desktop --home <QA_HOME>
```

`desktop` 需要该平台原生 WebView 依赖。若只检查系统浏览器，使用 `--action open`；
务必在报告中区分两者。已有独立测试目录可以继续复核，但首次上手检查必须使用新目录。

1. 设置操作者；进入设备与驱动，启用“模拟设备体验（无硬件）”，添加 Virtual VNA。
2. 打开操作页的 Python 示例，实际复制完整实验声明。核对提示：先查看需求，明确选设备、
   整机端点及生命周期策略，检查、准备，再显式运行。命名 setup 是可选复用入口。
3. 将原文保存到此应用登记的独立作者目录，显式刷新代码。使用独立参数分支，不选择命名
   setup。可参考[渐进绑定 Python 用法](progressive-instrument-bindings.md)。
4. 在实验准备页选 `capture`，查看需求，确认 `demo-readout` 与网络扫描接口。
   本次模拟策略明确选 `preserve`、`release`、`abort_and_release`，填写软件模拟场景及局限。
   这些选择只适用于本次模拟，不是物理设备策略建议。
5. 检查并准备后才运行；验证 51 点，再从 Data 打开和重开结果。Python 在同一独立应用中
   使用相同声明及共享绑定 API 再运行，核对两份精确运行身份，命名 setup 列表仍为空。
6. 导入带 setup 的配置时，准备两台同名、可读连接摘要不同的本地登记记录，明确选 B。
   核对名称、驱动型号、摘要、精确 ID；刷新后重新核对。提交使用当前列表的修订，
   并非选中时冻结；提交后设备修订已过期会由服务端拒绝，不自动改选 A。
7. 改选、清除或刷新绑定后须重新检查。遇到不支持的实体／拓扑／领域需求，应看到原因和
   明确 setup 的下一步，不把它解释为所有普通实验都必须先建配置。

停止仅限本次 `<QA_HOME>`，保留数据供对账：

```sh
uv run --locked scopecat app --action stop --home <QA_HOME>
```

报告完整 commit、平台、原生窗口或浏览器、应用目录、两个 run ID、receipt、实际点击步骤，
以及失败和未覆盖项。不要把 mock、CI 或此软件模拟结果写成真实硬件资格。
