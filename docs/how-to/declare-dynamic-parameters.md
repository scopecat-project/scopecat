# 从动态参数表开始 {#start-with-a-dynamic-parameter-table}

探索阶段的 Notebook 可以不定义 Python 类，直接声明表。以下假设已有作者会话和命名参数分支：

```python
import scopecat as sc

params = author.parameters.workspace("my-parameter-branch")
drive = params.declare_table(
    "drive",
    key="qubit",
    columns={
        "qubit": str,
        "duration": sc.column(float, unit="ns", minimum=4),
        "amplitude": float | None,
    },
)
drive["q0"] = {"duration": sc.Quantity(64, "ns"), "amplitude": None}
print(params.structure_diff())
print(params.diff())
version = params.save("drive-start")
```

即使表为空或某一列全为未知值，也必须显式声明类型。可使用 `bool`、`int`、`float`、`str`、
字符串 `Literal` 选项或 `EntityRef`；`sc.column` 可补充单位、边界或实体种类。
复合主键使用列名元组，主键列不能可空。字典行保留显式 Quantity 值，声明单位不会让读取结果变成裸数。

重新打开后用 `params["drive"]` 访问。重复相同声明不会产生新结构，
但写入相同主键会替换该行，保存前应审查差异。
此处 `params.save("drive-start")` 将编辑保存到名为 `drive-start` 的分支；后续重开应选择该分支。
继续保存当前分支可用 `params.save()`，无需每次另起名称。

## 扩展探索中的结构 {#extend-an-exploration}

修改结构前，先保存待处理的值编辑：

```python
params.add_column("drive", "quality", float | None)
params.declare_scalar("attempts", sc.column(int, minimum=1), value=3)
print(params.structure_diff())
params.save("drive-extended")
params.scalars["attempts"] = 5
```

新增列必须可空：已有行从未知值开始，不会填入假定的零。新标量必须提供显式初始值，
重复声明已有标量会被拒绝。重命名、单位换算和主键修改使用工作区已有的结构操作；
修改声明不会隐式改写数据，需要换算时应显式选择相应操作。
保存保留参数版本并推进或分出明确的分支，不发布实验室默认值。
[JSON 与 pandas 交换](exchange-parameter-tables.md)可填充已声明的表，导入不会推断新 schema。
最后一行是新的值编辑，仍需审查并保存。

## 构造符号引用 {#build-symbolic-references}

```python
duration = params["drive"].ref("duration", "q0", as_type=sc.Quantity)
```

引用携带表列的类型、单位和主键元数据，不读取当前单元格。因此，可以为未知值构造引用；
实验真正消费未知值时，仍会被已有验证拒绝。值由该次运行选中的配置提供，
后续 schema 编辑不会改变已经构造的引用。
向实验构建代码传入引用，不要在可移植实验定义中捕获活动工作区。

`as_type` 检查并收窄 Python 结果类型。不指定时，静态类型是 `ValueRef[object]`，
因为动态列名不能让类型检查器知道结果类型。复合主键按声明顺序组成元组；
受支持的符号主键输入仍保持符号形式。

## 实验稳定后采用模型 {#adopt-a-model-when-the-experiment-stabilizes}

```python
class Drive(sc.ParameterModel, table="drive"):
    qubit: sc.Param[str] = sc.param(key=True)
    duration: sc.Magnitude[float] = sc.quantity(unit="ns", minimum=4)
    amplitude: sc.Param[float | None] = sc.param(default=None)
    quality: sc.Param[float | None] = sc.param(default=None)


row = params[Drive]["q0"]
row.duration = 80
ref = sc.parameter_ref(Drive.duration, "q0")
```

匹配的模型绑定同一张已保存表，不复制或迁移数据。已有未知值仍然未知，构造器默认值不会补齐保存的单元格。
模型提供属性补全和静态类型引用；上例的 `row.duration` 按模型声明的 ns 单位读写数值，
与动态字典行保留 Quantity 的方式不同。属性赋值仍是待保存编辑。
共享实验室声明的写法见[参数模型](declare-parameter-models.md)。
