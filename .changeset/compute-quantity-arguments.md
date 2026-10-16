---
default: patch
---

# 提前反馈 compute 的 Quantity 参数错误

普通 Quantity 参数在构图时拒绝明显不匹配的实参，并提示 compute 和参数名；测量标量按该声明携带保留单位进入原生函数，已有 float 消费者保持不变。
