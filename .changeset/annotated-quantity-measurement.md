---
default: patch
---

# 修复显式 Quantity 参数的测量值适配

带 Unit 或 ScalarType 注解的 Quantity 参数及其类型别名，现在接收保留原单位的 Quantity，避免构图通过后调用 `.to()` 却收到 float。单位完全一致检查和普通 float 消费者行为保持不变。
