---
default: major
---

# compute 按消费侧单位契约归一化并拒绝隐式剥单位

带单位输入现在必须由消费函数声明单位语义。普通 Quantity 保留来源单位；带目标 Unit/ScalarType 的 Quantity 或 float、带目标 ArrayType 的数组先进行兼容线性换算，再传入稳定的原生 Quantity、float 或 NumPy 数组。原始测量数据不改写；错误量纲、轴/dtype 不匹配、整数截断和溢出会报错。

迁移：给旧的裸 float/数组消费者补 `Annotated[float, Unit("MHz")]` 或 `Annotated[NDArray[...], ArrayType(..., unit="MHz")]`；也可用 Quantity 适配函数显式 `.to("MHz").value` 后调用纯数值核心。`ctx.convert` 保留单位，不能单独替代消费侧声明。此前显式 compute 输入契约对不同前缀的 exact-unit 拒绝改为自动归一化；持久结果读取和输出契约不变。无单位数值路径继续可用，`.eager` 仍是接收预先准备好原生值的 Python 入口。
