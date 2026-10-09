# 合成 Rabi 实验项目

本教材保留为科学与内核回归夹具。普通学习从 Scopecat Help 开始；
命令行 `scopecat init --topic TOPIC DIRECTORY` 生成同类可编辑作者源码，
随后在当前应用 Settings 添加目录并准备作者环境。

现有独立目录请保留原软件环境、源码、笔记和数据；新版本不会自动迁移或覆盖它们。

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
