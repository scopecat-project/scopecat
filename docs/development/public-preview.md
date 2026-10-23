# 公开预览版与源码开发 {#public-previews-and-source-development}

公库发布桌面应用和框架 Python 产物。实验室提供普通源码或扩展 wheel、依赖锁和现场设置，
不需要重新打包桌面应用。产物消费者无需 public checkout、Git submodule、Node.js 或 pnpm；
应用环境与作者环境分别更新。

## 数字版本发行 {#numbered-releases}

日常合并运行 CI 并产生验证证据，不自动发布版本。
首个数字实验版本 [v0.1.0 / build 1](https://github.com/scopecat-project/scopecat/releases/tag/v0.1.0)
已于 2026-10-15 发布，对应源码 `efefef28c8b4b66b47c5849ddb954f5b317be711`。
该发行的 tag 和文件保持原样；下文说明当前代码用于**后续构建**的规则，不回写历史产物。

`release.toml` 是应用发行号和原生 build number 的权威来源。同批七个第一方 wheel
在构建时采用该发行号；UI 保留自己的内部版本表达。`0.0.0` / build `0` 仅是未发布的
bootstrap 标记，不能发布。准备下一版本时默认使用 Knope 根据 changefile 建议的版本，
也可显式指定版本覆盖；版本和 build number 都必须递增。

在 `.changeset/` 中记录用户可见的功能、修复或不兼容变化，默认使用中文；SDK、Notebook、
命令和配置名保留自然写法。可运行 `knope document-change`，也可直接编写文件。
简单变化用一句完整的话即可，生成后为列表项：

```markdown
---
default: patch
---

重新打开应用后保留选中的运行结果。
```

复杂条目以一个完整的 `# 短标题` 开头，空一行后说明用户影响和必要操作；不要默认写成长篇、
多标题文章：

```markdown
---
default: minor
---

# 重开 Notebook 后继续查看保存的结果

重新连接同一数据空间后，可用保存的运行 ID 查看原结果，无需重新采集。
请保留原始 ID，并选择 Scopecat 显示的作者解释器。
```

仓库固定的 Knope 0.23.1 将标题和正文整理为发布说明，默认沿用生成层级，不手工重复添加
版本或分类标题。标题保持在一个物理行；普通段落的硬换行可能使首行成为不完整标题。
详细条目使用标题与正文，简单句子使用列表项；参见 [Knope change 示例](https://knope.tech/reference/concepts/change/)。

功能使用 `minor`，不兼容变化使用 `major`；纯内部工作不必增加 changefile。
提交消息没有固定格式要求，`[changes].ignore_conventional_commits = true` 阻止 Knope
从提交消息推断版本。Knope 默认的 0.x 规则将普通功能/修复映射为 patch，不兼容变化映射为
minor；应在 release PR 中审查建议版本与说明，版本建议本身不是发布授权。

准备工作流通过固定完整 commit SHA 的官方 `knope-dev/action` 安装 `KNOPE_VERSION`。
Knope 的 [versioned files](https://knope.tech/reference/config-file/packages/)
配置只同步 `release.toml` 的发行号，并生成 `CHANGELOG.md`；wheel metadata 和同批精确依赖
由构建脚本在源码副本中写入，不靠 Knope 修改各包的源码版本。
[PrepareRelease](https://knope.tech/reference/config-file/steps/prepare-release/)
本身只在本地处理 changefile，不发布或提交。

Renovate 分别跟踪 action 的固定 SHA/版本和带注释的 CLI `KNOPE_VERSION`；现有
`githubActionsVersions` preset 识别 CLI 的 `knope/v...` tag。两者更新均需审查，
不自动合并；接受升级前应运行准备及 PR/release dry-run，不实际发布。所有工作流中的
CLI 版本须一致。预发布期间 Knope 保留 changefile，准备正式版本时再移除，使正式说明
包含完整变化。

### 发行候选的交付检查 {#first-numbered-delivery-readiness}

用户入口是[下载与安装](../getting-started/install.md)。已有 v0.1.0 不代表后续候选
自动取得同样资格；分发新候选时按实际变更复用有效证据，并补齐受影响范围：

- 明确发行号和原生 build number。工具存在或 CI 通过不等于已成功发布该版本。
- 根据[原生验收](native-window-acceptance.md)证据说明实际测试的 OS 与架构。
  实验性发行可只声明已测系统，不必先建立完整最低版本矩阵；打包默认值不构成支持证明。
- 在干净 Windows 目标上核对 WebView2 已安装和缺失 Runtime 的路径。安装器只复制应用
  与快捷方式，不捆绑或安装 Evergreen Runtime；承诺新电脑首次启动或离线安装前，
  应明确 Runtime 前置条件。
- 核对精确候选的下载、安装、教程与作者目录准备、一次合成运行、Quit、重开及继续旅程，
  包括准备/启动失败后的恢复，保留源码和回执。后端测试不能代替原生首次打开或编辑器观察。
- 在发行说明中记录签名/公证限制、外部编辑器与网络要求、数据兼容边界，核对实际资产与身份。
  不因改写指南而替换历史下载或扩大已验证平台范围。

可信签名不是实验性发行新增的必需门槛，但必须如实说明限制。
本清单不会运行 Prepare、Publish 或原生验收，也不是逐 PR 重跑完整验收的要求。

### 准备、审查与发布 {#prepare-review-then-publish}

发布前，以最后实际分发版本与最终候选的差异为准，合并重复条目，移除已撤回或被后续实现
取代的描述，复核不兼容变化和必要操作。changefile 是可编辑的发布输入，不是逐条保存
历史 PR 的日志；不要要求用户经过未发布的中间迁移。

使用工作流固定的 Knope 运行 `knope --dry-run prepare-release`，检查实际生成的标题、
正文结构、重复内容和迁移说明；此命令只报告建议，不写文件或发布。
在 release PR 中再次审阅 changelog，自动版本选择不能代替文字审查。

1. 随日常工作提交用户可见的 changefile。在干净 checkout 中安装工作流记录的
   `KNOPE_VERSION`，运行
   `uv run --locked python scripts/release.py prepare --build-number NUMBER`。
   可在 `prepare` 后加 `VERSION` 覆盖建议版本。该命令编辑并暂存发行号、build number、
   changelog 和已消费的 changefile，不 push、打 tag 或发布；提交前检查 diff。
2. 或在 main 手动运行 **Prepare release**，传入严格递增的 build number 和可选版本。
   工作流创建专用分支，在同一工作流内显式运行 CI（含文档），再用 Knope 的
   `CreatePullRequest` 和 changelog 模板创建普通 release PR。创建或合并该 PR 都不发布。
   不能假定默认 token 会触发另一工作流。验证失败时留下可审查分支，不创建 PR；修复后以
   新分支身份重试。自动化依赖仓库已有的创建 PR 权限，不创建凭据或修改权限；若禁止创建，
   使用本地准备和普通维护者 PR 流程。
3. 审查精确版本、build number 与用户说明，通过正常 CI 流程合并；这一步不创建 tag 或 release。
4. 对集成提交运行 **Full acceptance → full**，再以其完整 SHA 手动运行 **Publish release**。
   工作流检查 main 祖先关系和该精确提交的 full acceptance 成功结果，在同一工作流运行一次
   CI（含文档），构建框架与原生产物，再组装、导出和验证发行。此前的 CI 或 Pages 部署不是
   额外前置。只有最后的 job 可以写 release；检查拒绝已有 tag/release 后，Knope 的
   `Release` 按版本、changelog、预发布标记和配置的资产创建 tag/release，通过 draft 上传，
   全部上传成功才公开。若创建 draft 后失败，应停止并检查，不能自动覆盖资产或复用版本。
   没有自动修复或上传后重新下载校验；交给 Knope 前的完整本地资产验证是此流程的完整性保证。

每个候选（包括 alpha/beta/RC）使用新发行号和严格递增的 build number（1–9999，受共享的
Mac 单字段 build 限制）。版本支持 `X.Y.Z`、`X.Y.Z-alpha.N`、`X.Y.Z-beta.N`、
`X.Y.Z-rc.N`，N 为正整数。公共发行号不加 metadata 后缀，完整 commit 和文件哈希另行保留。
应用 tag 为 `v<version>`，版本历史先按 `v[0-9]*` 选择再严格校验；未来独立发行的包可使用
`<pkg>-v<version>`，当前未实现第二条发行线。历史 `preview-<SHA>`、`desktop-<SHA>`
及已发布数字版本保持不变。

### 产物身份与消费者选择 {#artifact-identity-and-consumer-selection}

后续数字发行使用当前 `release_assets.py` 的组装与导出契约：

| 公开文件 | 当前导出规则 |
| --- | --- |
| 两个平台安装器 | `Scopecat-VERSION-PLATFORM-ARCH.dmg` / `.exe`；平台为 `macos` / `windows`，架构为目标资格记录中的 `arm64` / `x86_64` |
| 七个第一方 wheel | 同批构建并校验 METADATA、精确第一方依赖和文件哈希 |
| `scopecat-ui.zip`、`preview.json` | 保留现有固定产物消费者和安装态验收所需的协议 |
| `release.json` | 绑定发行号、build number、完整 commit、文件哈希及原生资格证据 |

共 **12 项公开资产**。安装器名称必须与已验证目标一致，命名规则不增加支持的平台或架构。
四份 `macos/windows` 的 `bundle.json` 和 `native-release.json` 资格文档以原文嵌入
`release.json` 的 `qualification`；独立文件留在 Actions 构建证据中，不再单独导出。
`release.json` 保持 format-1 的 `preview` 和 `files` 契约，`files` 只列被索引的公开下载文件；
新增 `installers` 索引提供目标和校验和。导出后的验证仍检查嵌入证据的来源、目标、资源清单
和哈希。缺失、多余文件及错误 commit、版本、平台或哈希都会阻止组装/发布，不能只信文件名。

原生应用只保留 core/server 两个作者 wheel，并不意味着其余公开构建/测试消费者已迁移。
七个 wheels、UI ZIP 和 `preview.json` 在消费者迁移前继续保留；最终公开资产裁剪仍由
[#1030](https://github.com/scopecat-project/scopecat/issues/1030) 跟踪，本指南不宣称已经完成。

当前构建从 `release.toml` 为七个第一方 Python 包生成同一版本：

- 正式 wheel 使用干净 PEP 440，如 `0.1.0`、`0.1.0a2`、`0.1.0b1`、`0.1.0rc4`；
  公共 `alpha.N` / `beta.N` / `rc.N` 分别映射为 `aN` / `bN` / `rcN`。
- 开发 wheel 使用 `<release-PEP440>.dev<commit timestamp>+g<full commit>`；
  相同提交重复构建保持同一版本。完整 commit、build 与文件哈希继续由清单承载。
- 构建前在源码副本中写入 metadata，第一方运行依赖及 extras 精确锁定同批版本；第三方约束
  和下游适配包版本仍由各自维护。该规则不意味着发布到 PyPI。
- 从旧 `0.2` 包基底回到应用 `0.1` 系列是有意选择。使用新环境或显式选择新精确版本与哈希配方；
  普通 upgrade 不能表达降级，作者环境仍按内容身份管理。

UI 版本规则未改，继续以自身基底、公共版本和 commit 生成 SemVer prerelease。
Mac short version 是公共数字三元组，build 是显式 build number；Windows 文件版本为
数字三元组加 build。完整公共预发布身份保留在清单中。

**历史 v0.1.0 不适用上述新命名和 wheel 规则。** 它保留 `Scopecat.dmg`、
`Scopecat-Setup.exe`、四份独立平台清单及旧包名/版本，共 16 项资产；旧 wheel 使用各包基底加
`.dev<TIMESTAMP>+scopecat.<PUBLIC-PEP440>.g<SHA12>`。消费者必须按该发行的真实清单选择，
不能从当前规则猜文件名或要求重发；后续构建不会替换它的文件或 tag。

消费端应显式选择发行及可选的预期完整 SHA，核对 release/preview 身份和哈希后再更新
wheel URL 与依赖锁。选择新发行与通过实验室硬件验收是两回事；发行存在不会自动改变任何 pin。
保留的历史 SHA preview 仍可选择。已有消费端的 `tools/select_public.py` 使用
`--release VERSION` 和可选 `--commit FULL_SHA` 选择数字发行，直接传入 `FULL_SHA`
选择 SHA preview；这些是消费端入口，不是公库 checkout 命令。

本地构建与离线校验使用以下命令；组装前须把两平台已验证的安装器、四份资格文档和全部
公开框架产物放入同一个平面输入目录，具体布局见发布工作流：

```sh
uv run --locked python scripts/build_preview.py build/preview --ref FULL_SHA --release
# 将完整构建输入汇集到 build/release-inputs 后：
uv run --locked python scripts/release_assets.py assemble build/release-inputs --commit FULL_SHA
uv run --locked python scripts/release_assets.py export build/release-inputs --output build/release --commit FULL_SHA
uv run --locked python scripts/release_assets.py verify build/release --commit FULL_SHA
```

`export` 要求新的输出目录。测试使用合成文件验证拒绝路径，不写网络；这不证明实际原生发行
已构建或发布。**Full acceptance → native-distribution** 的原生 Actions 产物保留 14 天，
不是持久发行。不带 `--release` 的普通构建仍可生成按 commit 标记的开发产物。

### 仅框架的 preview 交付 {#existing-framework-only-preview-delivery}

**Full acceptance → public-preview** 仍是显式、持久的仅框架发布入口。
精确提交通过 CI 后，手动选择该 profile，发布 wheels、UI 和 `preview.json` 到
`preview-<full SHA>`。它不要求原生安装器，也不随合并自动运行；既有 SHA 发行及 pin
保持可读和不可变，新 SHA preview 仍可发布。

数字发行用于完整应用与框架一起交付；两条路径保留同一 URL/hash 选择契约、精确 wheel URL
和锁哈希。`scripts.app_build.preview` 与 delivery `--preview` 读取已选 URL/hash，
不假定 tag 格式。`build_preview.py` 与 delivery `--public-artifacts` 共用构建和校验代码，
不另建包版本平台。

仅框架交付是否也采用数字版本、是否统一框架与原生发行节奏仍是后续决策。
Knope 和同批 wheel 版本规则没有取消现有 preview 能力，也没有增加原生安装器前置。

### 验证边界 {#qualification-boundaries}

原生应用自带 Python 与依赖；作者依赖、厂商 SDK/固件分别交付，离线使用须在目标平台验证。
参见[安装与维护](../how-to/maintain-application.md)、[Mac 首次打开](../how-to/mac-preview.md)
和[打包验证](architecture/desktop-packaging.md)。Windows 仍未签名，Mac 为 ad-hoc
签名，无可信签名或公证。应用是实验性的，不用于生产；普通数字版本不改变该状态。
alpha/beta/RC 由 Knope 按版本身份设置 GitHub prerelease 标记，生产适用性须另行说明。

尚未指定持久数据支持基线。发行号不是数据 schema 版本，也不承诺读取旧开发格式；遵循
[数据兼容政策](data-compatibility.md)，区分当前格式恢复、不支持格式的拒绝和未来兼容承诺。

## 不安装应用的源码运行 {#run-without-installation}

### 日常源码桌面 {#daily-source-desktop}

安装 Python 3.14+、uv、Node.js 和 pnpm，在 public checkout 中运行：

```sh
uv run --locked python -m scripts.dev
```

该入口打开现有 pywebview 原生桌面，使用 Vite HMR 和真实 Python 后端。
macOS 首次准备缓存的 **Scopecat Dev** `.app`，使用现有图标和稳定的 worktree 专属
bundle 身份；需要 Xcode command line tools（`xcode-select --install`）。小型宿主在进程内
链接当前 Python，保留环境、stdin、信号和退出码，不把 Python、依赖、源码或 GUI 复制进
`.app`。可通过打印的宿主路径在原生工具中识别它；它不是可分发安装包。

Windows 同样准备缓存的 **Scopecat Dev.exe**，使用与已安装应用分离的 worktree 专属
AppUserModelID。安装包含 Windows SDK 的 Visual Studio C++ Build Tools
（Desktop development with C++）；入口发现已安装编译器，或使用当前 Native Tools prompt。
窗口属于该可执行文件而非 Python。宿主在进程内加载当前 Python，保留 editable 虚拟环境，
后端子进程使用普通 Python 可执行文件。它继承开发终端供重启命令和 Ctrl+C 使用，等待中的
launcher 转发退出码，无需应用安装包或复制 Python 环境。

宿主缓存只随 checkout、解释器、ABI/libpython、图标或宿主构建输入变化。
普通 GUI 和应用源码编辑不重建宿主；修改宿主自身输入后，先安全退出既有进程再启动。
`--browser` 跳过原生宿主，仅供内部检查。

源码桌面首次启动仍调用锁定交付构建器，再投影为按内容身份缓存的 format-2 作者资源：
core/server 两个 wheels、依赖锁、`author.json` 配方及有限工具文件，不保留独立 Python
运行时。Settings 准备作者环境时使用显式选择的 Python，或按工作区/配方固定版本查找
uv 管理的 Python；缺少缓存时可能下载，离线缺失则明确报错，不复制应用自身的 Python。
这段说明针对源码桌面的资源准备成本，不把构建中间产物当作已安装应用的作者环境。
缓存身份包含包源码与依赖/构建输入，身份变化可能在启动或重启时触发重新准备。
UI 编辑使用 HMR，不重建 wheels、作者资源或应用。
源码 Python/包变化会提示重启：输入 `r` 重启空闲后端，或 `w` 等待空闲后再重启；活动或
不确定工作会阻止重启，不强制停止。原生宿主 Python 变化需完整安全退出后再启动。
既有作者 `.venv` 和保留的执行环境不会静默跟随 checkout 更新。

资源准备不生成 DMG 或 Windows 安装器，无需每次源码验证都打包。
打包路径用于安装交付验收，源码桌面可运行不证明下载的安装器可用。

默认 home 位于用户的 `Scopecat-Development` 数据目录，以规范化 worktree 路径的稳定哈希
区分，处于 checkout 外并与已安装应用数据分离。每个 worktree 有自己的进程归属和动态端口；
重复启动复用现有进程并激活原生窗口。应用从空白状态开始，不自动创建或登记示例作者目录，
通过 Settings 创建或登记普通源码。

```sh
# 独立保留的空白试用目录：路径须为新目录，或已归当前 checkout 所有。
uv run --locked python -m scripts.dev --home /absolute/path/to/dev-trial
# 在 OS 临时目录中新建空白 home，不自动删除。
uv run --locked python -m scripts.dev --temporary
```

启动时打印源码、home、UI/后端 URL 和日志位置。输入 `q` 后回车或按 Ctrl-C，会请求等工作
空闲后退出；`c` 取消终端中的等待。原生 Quit 沿用应用按工作状态处理的退出流程；选择后台
继续工作会隐藏窗口并保持 launcher 运行。Vite 退出时终端报告失败和日志路径，后端工作继续；
重启前用 `q` 或 Ctrl-C 安全退出。历史不会重置或自动删除；有用的临时 home 数据应在 OS
清理前保存到临时目录之外。

`--browser` 使用同一 Vite UI 与真实后端，需手动打开打印的 URL；原生 bridge 功能不可用，
它不是另一套浏览器产品或替代 UI。Vite 使用有归属的 endpoint 文件，在安全重启后跟随后端端口。

安装包验收使用[本地桌面构建](local-desktop-trial.md)，与日常源码编辑分开。
原生 HMR、bridge、多窗口和 Quit 行为仍须在目标 OS 观察，云端后端测试不能代替。
