# 合成 Rabi 实验项目

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

本轮项目仍使用本地存储。正式实验室数据空间与代码工作区解耦是后续开发，不把该
教学模板视为已经完成多实验台或持续数据管理的部署。

第二批小范围练习见 [修改、刷新、读取](EDITING.md)：先改一个默认值，
再另选时间尝试平均 IQ 与类型化历史读取。两项不要求同时完成，也不包含原课程全部题目。

参数保存、扫描/分组与研究目录分题见 [GROUPS.md](GROUPS.md)；
维护者无 AI 备份恢复专项见 [MAINTENANCE.md](MAINTENANCE.md)。
旧 A/B 练习项目保持固定；这些题在对应新版交付的新项目中另做。
