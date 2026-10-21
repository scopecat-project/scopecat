---
default: minor
---

# Flux SOS IIR 波形计算

新增稳定 float64 SOS 核心、显式连续分块状态与单指数相对阶跃畸变的离散逆补偿入口。Flux v2 按静态矩阵 → IIR → 可选 FIR 执行，使用显式尾长和无限未来残差界检查，复用现有快照、预览/下发及资源和幅值约束。独立 shot 复位；非零 timing 与 flux/filter、非零 DC、自动校准及硬件滤波仍不支持。
