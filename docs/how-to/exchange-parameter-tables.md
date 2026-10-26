# 用 JSON 或 pandas 编辑参数表 {#edit-parameter-tables-with-json-or-pandas}

完成[参数声明](declare-parameter-models.md)后，从具名参数工作区导出可编辑副本。
导入先生成差异，应用差异才会手工编辑该工作区，保存则创建具名的保存版本。
这些操作不会激活默认配置，也不提供验证证据；测量结果的候选与独立验证见[候选验证](verify-parameter-candidates.md)。

## JSON：可移植的编辑文件 {#json-a-portable-editable-file}

```python
from pathlib import Path

params = author.parameters.workspace("my-parameter-branch")
drive = params[Drive]
path = Path("drive.json")
path.write_text(drive.export_json(), encoding="utf-8")
```

编辑文件中的 `rows` 数组，保持 `format`、`definition` 和 `base` 不变：
它们描述表的 schema、单位和导出时的精确草稿。然后预览、应用并保存：

```python
pending = drive.preview_json(path.read_text(encoding="utf-8"))
for change in pending.diff:
    print(change.key, change.field, change.before, change.after)
pending.apply()
print(params.diff())
version = params.save("drive-import")
```

`preview_json()` 验证整个导入，不改变工作区。`apply()` 在编辑前再次检查基线。
如果自导出或预览后表已被编辑，或工作区已保存，应重新导出并审查新基线。
基线标识导出表的 schema 和值；导入检查这些输入，不将表绑定到样品或装置配置，
不转移科学验证，也不迁移项目配置。

只应用发生变化的单元格。未修改的单元格保留其存储单位表示和来源。
导入变更属于手工编辑；复制托管分析的元数据不能把它们变成已验证候选。

## 单元格与行规则 {#cell-and-row-rules}

| 输入 | 含义 |
| --- | --- |
| 数值零 | 已知的零，仍受声明边界约束 |
| JSON `null` | 显式将单元格清为未知值 |
| 省略非主键字段 | 保留已有值；新行中则保持未知值 |
| 省略行 | 保留该行，除非显式要求 `delete_missing=True` |
| 新行 | 插入该行；必须提供所有主键字段 |

编辑时，必需字段可以保持未知值；后续实验消费该字段时，仍会停止并报告具体字段错误。
模型构造函数默认值不会填充导入中省略的字段。重复主键、多余列、无效选项、不兼容单位和越界值都会被拒绝。

物理量列按 **存储 schema 的单位** 导出数值，单位记录在 `definition` 中；
模型视图使用其他显示单位不会改变这一约定。实体主键保留其种类与元数据，`"001"` 等字符串标识仍为字符串。
重命名主键或列、改变单位时，使用显式工作区结构编辑，不要修改交换元数据。

删除行需要调用 `drive.preview_json(text, delete_missing=True)` 并审查产生的行删除。
应用删除会使先前的行视图失效。

## 可选的 pandas 编辑 {#optional-pandas-editing}

如果尚未安装 pandas，将其安装到作者内核使用的环境。JSON 交换不导入也不依赖 pandas。

```python
frame = drive.to_dataframe()
frame.loc[frame["qubit"] == "q0", "duration"] = 96
pending = drive.preview_dataframe(frame)
print(pending.diff)
pending.apply()
version = params.save("drive-spreadsheet-edit")
```

导出的 DataFrame 使用 object 列以保留标识与未知值。主键仍是普通列，DataFrame 索引会被忽略。
保留 `frame.attrs["scopecat.parameters"]`；若转换操作丢弃它，必须从重新导出开始。
CSV 往返不能保留这份元数据；可移植文件使用 JSON。

`None` 和 `pd.NA` 显式清空单元格。浮点 `NaN` 会被拒绝，因为它既可能表示缺失数据，也可能表示无效数值结果。
如果确实要表示未知值，显式使用 `preview_dataframe(frame, nan_as_unknown=True)` 并审查差异。
删除行同样需要显式的 `delete_missing=True` 选项。

类型化行见[参数声明](declare-parameter-models.md)。需要托管来源与发布流程的测量更新，
继续阅读[候选验证](verify-parameter-candidates.md)；保存版本不能替代候选的验证证据。
