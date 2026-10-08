# 合成 Rabi 实验项目

此路径用于维护者独立验收和既有独立项目；普通学习推荐从 Scopecat Help 开始。

用 VS Code 打开本文件夹，安装推荐的 Python/Jupyter 扩展。在 Notebook 右上角选择
本项目 `.venv` 的 Python，打开 `notebooks/start.ipynb`；完成后在新内核运行
`notebooks/reopen.ipynb`。不需要 sys.path、切换工作目录或启动浏览器 JupyterLab。

维护者交付软件后，运行 VS Code 任务“首次准备项目环境”（或在交付环境执行
`scopecat-lab prepare <本目录>`），创建项目环境
并安装本地实验包。`.venv` 已存在时不会覆盖；已有环境的日常打开不重复 prepare。

运行 Notebook 前，在 VS Code 的 Terminal → Run Task 选择“启动实验服务”，或选择
“打开实验界面”（先启动/复用服务，再打开 GUI）。完成后运行“停止实验服务”。
关闭编辑器或 Notebook 内核不会停止 daemon，也不会取消已经提交的实验。

| 内容 | 用途 |
|---|---|
| `src/my_experiment/teaching.py` | 你编写的实验，普通 Python 包 |
| `notebooks/` | 调用、图、观察，以及重开运行的书签 |
| `pyproject.toml` | 本地实验包的声明；环境由交付工具准备 |
| `.vscode/` | 扩展推荐、解释器提示、首次准备与服务任务 |
| `.venv/` | 本项目的 Python 环境，不手工复制到别的项目 |
| `src/workspace_app.py`、`scopecat.toml` | 初始配置、声明式实验能力和源码捕获范围 |
| `.scopecat/` | 参数历史、run 与来源证据，不手工编辑 |
| `author-environment.json` | 交付软件身份记录 |

项目只使用合成响应，不连接设备。只有 `teaching_drive` 一张参数表；默认频率是教学
输入，共振频率 5.145 GHz、pi 幅度 0.24 arb 是夹具真值，不是实际样品的测量结果。
首次连接和修改 `src/my_experiment/teaching.py` 并保存后，都在 Notebook 执行：

```python
session.refresh()
from my_experiment.teaching import teaching_rabi
```

然后重新运行创建 `request` 和 `prepared` 的单元。新请求使用新代码与默认值；已经创建的
请求、预览和结果保留原版本，不会因为刷新变成新实验。不要只重跑旧 `request` 的 prepare。
不需要另写 `importlib.reload`。共享软件包升级由维护者处理，并重启 Notebook kernel。
如果刷新报语法错误，修正文件后再次刷新；不要把刷新成功当成实验已经运行。

这是命令行生成的独立教学项目，运行与历史保存在本目录。Scopecat Help 的课程使用
当前应用的数据空间；其“继续”不会打开本项目。再次使用本项目时打开原目录并启动服务。

第二批小范围练习见 [修改、刷新、读取](EDITING.md)：先改一个默认值，
再另选时间尝试平均 IQ 与类型化历史读取。两项不要求同时完成，也不包含原课程全部题目。

参数保存、扫描/分组与研究目录分题见 [GROUPS.md](GROUPS.md)；
维护者无 AI 备份恢复专项见 [MAINTENANCE.md](MAINTENANCE.md)。
旧 A/B 练习项目保持固定；这些题在对应新版交付的新项目中另做。

## 可编辑的课程源码

`src/my_experiment/parameters.py` 声明参数，`setup.py` 初始化参数分支，
`response.py` 定义合成响应，`teaching.py` 定义实验，`analysis.py` 和 `session.py`
定义分析与报告。修改后执行 Notebook 的 refresh/import 单元；历史运行保留原始源码。
新建入口不会覆盖已有目录。旧课程请保留原环境，或手动把自己的修改迁入新目录。
