# 公开预览版与源码开发 {#public-previews-and-source-development}

公库发布桌面应用和框架 Python 产物。实验室提供普通源码或扩展 wheel、依赖锁和现场设置，
不需要重新打包桌面应用。产物消费者无需 public checkout、Git submodule、Node.js 或 pnpm；
应用环境与作者环境分别更新。

## 数字版本发行 {#numbered-releases}

日常合并运行 CI 并产生验证证据，不自动发布版本。
已发布版本及其固定资产以 [GitHub Releases](https://github.com/scopecat-project/scopecat/releases)
为准。v0.1.0 使用历史 16 项布局，v0.2.0/v0.2.1 使用 12 项布局；
下文说明当前代码用于**后续构建**的 7 项规则，不回写历史 tag、文件或哈希。

`release.toml` 是应用发行号和原生 build number 的权威来源。同批七个第一方 wheel
和正式 UI 在构建时采用该发行号；完整源码 SHA 保留在独立构建溯源字段。`0.0.0` / build `0` 仅是未发布的
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

工作流固定的 Knope 版本（`KNOPE_VERSION`） 将标题和正文整理为发布说明，默认沿用生成层级，不手工重复添加
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

### 发行准备范围 {#post-020-patch-readiness}

已发布功能与验收记录保留在 [CHANGELOG](https://github.com/scopecat-project/scopecat/blob/main/CHANGELOG.md)
和相应 Release/PR，不在本指南维护第二份“下一版本”清单。
准备候选时以最后实际分发版本为基线，审查 `.changeset/` 和实际差异，
按下方流程选择受影响验收；工具或 PR 通过不代表新版本已发行。

### 发行候选的交付检查 {#first-numbered-delivery-readiness}

用户入口是[下载与安装](../getting-started/install.md)。已有发行不代表后续候选
自动取得同样资格；分发新候选时按实际变更复用有效证据，并补齐受影响范围：

- 明确发行号和原生 build number。工具存在或 CI 通过不等于已成功发布该版本。
- 根据[原生验收](native-window-acceptance.md)证据说明实际测试的 OS 与架构。
  实验性发行可只声明已测系统，不必先建立完整最低版本矩阵；打包默认值不构成支持证明。
- 在干净 Windows 目标上核对 WebView2 已安装和缺失 Runtime 的路径。安装器替换应用资源并创建
  快捷方式，不捆绑或安装 Evergreen Runtime；承诺新电脑首次启动或离线安装前，
  应明确 Runtime 前置条件。此项有限观察由 [#1416](https://github.com/scopecat-project/scopecat/issues/1416) 跟踪。
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
| 四个公开 wheel | `scopecat`、`scopecat-server`、`scopecat-instruments`、`scopecat-quantum`；校验 METADATA、精确第一方依赖和文件哈希 |
| `release.json` | 绑定发行号、build number、完整 commit、文件哈希及原生资格证据 |

共 **7 项公开资产**。安装器名称必须与已验证目标一致，命名规则不增加支持的平台或架构。
四份 `macos/windows` 的 `bundle.json` 和 `native-release.json` 资格文档以原文嵌入
`release.json` 的 `qualification`；独立文件留在 Actions 构建证据中，不再单独导出。
`release.json` 使用 **format 2**，`packages` 只列四个公开包，`files` 只列六个下载文件，
不再引用 `preview.json`；`installers` 索引提供目标和校验和。导出后的验证仍检查嵌入证据的来源、目标、资源清单
和哈希。缺失、多余文件及错误 commit、版本、平台或哈希都会阻止组装/发布，不能只信文件名。

#### release.json 读取合同 {#release-manifest-contract}

`release.json` 是数字发行的公开清单入口；消费方显式选择发行，再固定该清单的 SHA256
与预期完整源码 SHA。`format: 2` 的 `version`、`build_number`、`commit` 标识同一批产物。
拒绝不支持的格式、重复 JSON key、无效身份与不一致摘要；文件名必须是无目录的本地文件名。
`files` 是文件名到 SHA256 的映射，不包含 `release.json` 自身，不能依靠自摘要建立信任。
四个包的版本必须与发行的 `wheel_version` 一致，channel 必须为 release。
wheel 还须验证 METADATA 和同批第一方依赖。历史 format 1 数字发行保留 preview 引用，
读取其 preview 时继续核对清单摘要、commit 和发行版本；历史资产不会改写。

只消费四个 wheels 的读取器无需下载安装器，但仍须验证所选文件的字节与清单引用关系；
这不等于完整发行资格。维护者的 `release_assets.py verify` 检查完整集合，需要对应源码
中的 `release.toml`；它不是接受缺失文件的子集下载器。`installers` 提供平台、文件名与摘要，
不能将目标平台记录解释为完整系统版本或硬件资格。

未来构建的 `*-native-release.json` 使用 **format 2**，外层 release 也使用 format 2：

| 字段 | 当前含义 |
| --- | --- |
| `channel` | 由 shipped bundle 身份确定：`development`、`prerelease`（alpha/beta/rc）、`release`；不是稳定性或签名承诺 |
| `signing.application` | 当前构建策略：Mac `ad-hoc`、Windows `unsigned` |
| `signing.installer` | 当前两平台安装器均 `unsigned`，不表示已完成可信签名验证 |
| `notarization` | Mac `not-notarized`，Windows `not-applicable` |
| `native_interaction` | 此资格报告为 `not-evaluated`；不把独立原生窗口验收并入 CI 的覆盖声明 |

生成器仅用于当前受控打包流程，不是任意第三方安装器的签名检查工具；新增可信签名或公证
必须同时补真实验证和合同。format 2 不再输出混合 channel/签名状态的 `distribution` 文案。
读取器按子文档自己的 format 分派，拒绝不支持的格式；format 1 历史证据原文及摘要保持不变，
其 `distribution` 不能作为结构化 channel 或可信签名证明。完整验证同时接受历史 format 1
和当前 format 2；后者拒绝与来源身份、当前构建策略或交互覆盖不符的声明。

公库组装先验证七个 wheel、UI、内部 preview 清单和两平台资格，再导出公开子集。
app、tutorials、testkit wheels、UI ZIP 和 `preview.json` 只保留在 Actions 候选材料中，
供构建及显式开发者验收。联合测试通过公库源码环境或明确准备的内部 runtime 执行；
不再以这些测试需求维持另一套公开安装入口。私库选择器固定四个公开包，支持新 format 2
及已发布的当前包名 format 1 数字发行，退役历史 SHA 选择和 lab-tools/lab-teaching 包族。

当前构建从 `release.toml` 为七个第一方 Python 包生成同一版本：

- 正式 wheel 使用干净 PEP 440，如 `0.1.0`、`0.1.0a2`、`0.1.0b1`、`0.1.0rc4`；
  公共 `alpha.N` / `beta.N` / `rc.N` 分别映射为 `aN` / `bN` / `rcN`。
- 开发 wheel 使用 `<release-PEP440>.dev<commit timestamp>+g<full commit>`；
  相同提交重复构建保持同一版本。完整 commit、build 与文件哈希继续由清单承载。
- 构建前在源码副本中写入 metadata，第一方运行依赖及 extras 精确锁定同批版本；第三方约束
  和下游适配包版本仍由各自维护。该规则不意味着发布到 PyPI。
- 从旧 `0.2` 包基底回到应用 `0.1` 系列是有意选择。使用新环境或显式选择新精确版本与哈希配方；
  普通 upgrade 不能表达降级，作者环境仍按内容身份管理。

正式 UI 直接使用应用发行号（如 `0.2.0`），`build-info.json` 的 `source_commit` 单独保存
完整源码 SHA；开发构建仍保留可追溯身份，不再给正式 UI 加旧基底前缀。
Mac short version 是公共数字三元组，build 是显式 build number；Windows 文件版本为
安装器的数字三元组加 build，不表示 payload EXE 带有 FileVersion/ProductVersion。
完整公共预发布身份保留在清单中。

**历史 v0.1.0 不适用上述新命名和 wheel 规则。** 它保留 `Scopecat.dmg`、
`Scopecat-Setup.exe`、四份独立平台清单及旧包名/版本，共 16 项资产；旧 wheel 使用各包基底加
`.dev<TIMESTAMP>+scopecat.<PUBLIC-PEP440>.g<SHA12>`。消费者必须按该发行的真实清单选择，
不能从当前规则猜文件名或要求重发；后续构建不会替换它的文件或 tag。

消费端显式选择数字发行及可选的预期完整 SHA，核对清单和所选 wheel 的身份/哈希，
再更新依赖声明与锁。历史 SHA preview 只供匹配历史 checkout 复现，不再是当前选择入口。
发行存在不会自动改变消费端 pin，也不代表实验室硬件验收通过。

本地构建与离线校验使用以下命令；组装前须把两平台已验证的安装器、四份资格文档和全部
内部 wheel/UI 构建材料放入同一个平面输入目录，具体布局见发布工作流：

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

### 内部候选材料 {#existing-framework-only-preview-delivery}

**Full acceptance → candidate-artifacts** 只生成内部候选材料。
精确提交通过 CI 后，手动选择该 profile，上传 wheels、UI 和 `preview.json` 为
`scopecat-candidate-<完整 SHA>` Actions artifact，保留 14 天，不创建 tag 或 GitHub Release。
开发者下载到明确目录后通过现有本地候选测试验证，不更新日常配方或冒充正式发行。
需要长期保留的证据应另行归档；临时 artifact 不作为作者依赖的持久 URL。

正式用户采用数字发行。已经发布的 `preview-<SHA>` 文件及历史 pin 保持不变，
不再发布新的仅框架 SHA release。`build_preview.py` 仍是内部构建入口，
delivery `--public-artifacts` 可消费同批候选，不要求先构建原生安装器。

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

源码桌面直接准备 format-2 作者资源：复用锁定依赖约束导出和版本标记后的
core/server 两个 wheel 构建，通过 `AuthorInputs` 投影并校验 Vite 引导页、约束和
`author.json` 配方。它不再调用完整 delivery，不准备第三方 wheelhouse、便携 Python
离线归档或 `install.py`；宿主使用开发环境已有的锁定 Python/uv。
Mac/Windows 仅为原生宿主另行取得锁定的 pywebview wheel，沿用已审查修补、哈希验证和
独立提取缓存，不把它放入作者 payload。损坏的作者 payload 或宿主 wheel 会在暂存目录
重建并验证后替换；准备期间源码变化则拒绝发布。

Settings 准备作者环境时使用显式选择的独立 Python，或按工作区/配方固定版本查找
uv 管理的 Python；缺少缓存时可能下载，离线缺失则明确报错，不复制应用自身的 Python。
源码桌面宿主 Python 与作者 client/执行环境仍各自持有独立环境，既有作者环境不随资源
缓存重建而改变。源码开发使用此投影；原生构建输入仍为 format 1 完整材料，公开 release.json 为 format 2。
通用 `install.py` 已退役，教学验收消费明确准备的 runtime 与 format 2 作者资源。
缓存身份包含包源码与依赖/构建输入，身份变化可能在启动或重启时触发重新准备。
UI 编辑使用 HMR，不重建 wheels、作者资源或应用。
源码 Python/包变化会提示重启：输入 `r` 重启空闲后端，或 `w` 等待空闲后再重启；活动或
不确定工作会阻止重启，不强制停止。原生宿主 Python 变化需完整安全退出后再启动。
既有作者 `.venv` 和保留的执行环境不会静默跟随 checkout 更新。

Linux 单次有界对比（2026-10-26，Python 3.15.1、uv 0.13.5，同一 checkout、已有工具缓存、
新的资源目录）：新路径首次 0.445s、复用 0.043s；旧路径首次 62.65s、复用 0.066s。
实际记录的新路径为一次锁导出和两次源码 wheel 构建；旧路径先准备便携 Python，构建五个
本地 wheel，再准备共 84 个 wheels 的离线交付后投影。新/旧作者 payload 为约 1.63/39.17 MB，
旧 payload 还复制了 uv 工具。此样本包含不同下载/缓存成本，不是跨机器耗时承诺。

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
