# 不依赖接线数据库记录装置观察 {#record-apparatus-observations-without-a-wiring-database}

要把一条物理线路、线缆、端口组等对象的测量与笔记放在一起查找，可使用装置历史，无须先建立完整连接图。
在 Scopecat 当前模型中，装置对象用于非样品部件的描述性历史，样品目录保留样品身份；
`InstrumentRef` 则表示带类型化客户端契约的仪器引用，用于仪器访问与控制声明。
这些是项目内不同记录和 API 的职责边界，不是自然语言中“装置”“样品”“仪器”永远互斥的分类。
同一条线路在不同实验中的角色可以变化，物理对象身份仍可延续；关联历史不会自动改变运行的科学主体。
不要仅为保存线路笔记而虚构一个样品。

这些记录描述历史：连接备注不证明当前接线，室温测量附件也不因此成为合格的低温校准。
Python 和 HTTP API 用于写入历史；工作台的 **数据（Data）→ 实验装置历史** 只读浏览。
将装置对象选作可执行实验的主体仍是另一项工作；记录历史不会改变既有样品身份、启动选择或当前生效配置。

## 在数据页浏览已保存的历史 {#browse-retained-history-in-data}

1. **查找对象。** 打开 **数据（Data）→ 实验装置历史**，按名称、别名或对象 ID 输入查询，
   点击 **搜索对象**。搜索仅限当前数据空间的目录。名称相同不代表同一对象；
   核对结果中的对象 ID 和目录，再明确选择对象。
2. **选择观察。** 在观察列表中选择记录，需要时点击 **更早观察** 翻页。
   详情中的对象描述按该观察保存的精确 `catalog_id`、`object_id`、`revision` 和 `content_hash` 解析，
   不用搜索结果中的当前描述替代。核对已知条件、备注，以及分别显示的观察时间和记录时间。
3. **下载附件。** 点击 **下载原文件** 后才下载附件的原始字节。
   照片和幻灯片不会在页内预览或执行；同名文件应结合内容哈希区分。
4. **打开精确运行。** 在 **关联 run** 中点击 **打开 run：…**，打开的仅是该观察所记录的确切运行 ID，
   不会按名称选择另一条运行。
5. **返回继续阅读。** 使用后退/前进，或返回数据页，可继续使用保留的对象、观察、搜索和分页状态；
   从运行返回时，保留的数据视图也会恢复阅读位置。

历史按记录顺序分页。观察时间（`observed_at`）与记录时间（`recorded_at`）分开保存；
不知道观察时间就保持未知。更正记录会链接到原记录，即使原记录在其他页也可打开，两条记录都保留可读。
当前页没看到更正，不表示该观察没有后续记录，也不证明它目前仍有效。

对象、精确描述或观察缺失时会明确报错，不会自动选第一条或最新记录替代。
切换数据空间后须重新明确选择，不会加载另一个空间的同名对象。

已有浏览器用例覆盖同名线路、描述修订、跨页更正、同名但字节不同的附件、精确运行跳转与返回，
并检查浏览过程只发出允许的读取请求。该用例不验证原生桌面下载对话框的保存或取消行为。

## 登记并查找线路 {#register-and-find-a-line}

以下假设已有连接好的 `LabClient`，变量名为 `lab`。
也可使用 `sc.notebook()` 返回会话的 `session.apparatus`，默认采用会话所选操作人员：

```python
line = lab.apparatus.create(
    "fridge-2-line-3",
    name="Fridge 2 input line 3",
    kind="microwave-line",
    aliases=("L3", "old blue cable"),
    actor="Li",
)
found = lab.apparatus.search("blue cable")
```

名称和别名用于发现对象，包含目录身份的对象引用才确定身份。重命名会创建描述修订；
观察保留原来的精确修订，后续改名不会改写该观察记录时引用的描述。系统不会按名称自动匹配物理身份。

## 保留原文件与已知条件 {#keep-the-original-file-and-known-conditions}

```python
from pathlib import Path

slides = lab.apparatus.import_file(Path("room-temperature.pptx"))
observation = lab.apparatus.observe(
    line.ref,
    title="Room-temperature transmission measurement",
    actor="Li",
    conditions={
        "temperature": "room temperature; exact temperature not recorded",
        "connection": "VNA ports 1–2; see slide 3",
    },
    note="Reference only. Cryogenic transfer has not been established.",
    attachments=(slides,),
)
```

导入操作读取 Python 客户端所在计算机上的文件，将原始字节及内容哈希保存到连接的数据空间。
服务端不会把文件名当作路径打开。文件仅作为附件下载，不在页面中渲染或执行；每个文件最多 64 MiB。
数据空间持有的附件字节包含在[当前格式的恢复副本](backup-and-restore.md)中。

`observed_at` 是可选的、带时区的测量时间。未知时省略；`recorded_at` 单独保留服务端记录时间。
条件是可选的人工描述，不是经过验证的物理状态模型。同一条线路的低温观察也应单独建记录，写明它自己的条件。

## 查找历史并关联已保存的测量 {#find-history-and-relate-retained-measurements}

```python
page = lab.apparatus.history("fridge-2-line-3", limit=50)
for item in page.items:
    print(item.id, item.draft.title, item.draft.conditions)

linked = lab.apparatus.observe(
    line.ref,
    title="Reference considered during this experiment",
    actor="Li",
    run_ids=(run.id,),
    note="Associated for review; not an assertion that this run calibrated the line.",
)
```

对象搜索匹配当前 ID、名称和别名。历史使用游标按记录顺序分页，不会用未知的测量日期编造时间顺序。
关联的运行 ID 必须已存在于此数据空间。关联不会修改运行记录的科学主体、参数来源或校准状态。
可用运行筛选查找哪些观察引用了某条已保存的测量。

观察记录只追加、不覆盖。更正备注时创建新观察并设置 `supersedes=observation.id`，
原记录仍留在历史中并可读取；更正必须针对同一个对象。
网络请求结果不确定、需要重试时，应事先选定并复用显式 `observation_id`：
内容完全相同的重试返回原记录，内容不同则报冲突，不会覆盖。

## 参考信息不等于已接受的校准 {#reference-information-is-not-an-accepted-calibration}

这些记录只是参考信息。将其中的数值用于实验，需要显式的参数操作和与该路径相符的来源记录。
自动复用校准还需要明确的适用性契约及合格证据；保存装置观察不会自动完成其中任何一步。
装置配置、参数归属和校准发布的职责划分提案见[装置历史设计](../development/architecture/apparatus-history.md)。
