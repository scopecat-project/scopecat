---
default: minor
---

# 原生 flux 校正基础

增加显式版本快照、静态电压串扰矩阵与逐输出 FIR 的完整记录实现；参考 list-mode target 支持零 DC 的 flux-only 记录，预览和执行共用最终缓冲区，检查尾长、电压、DAC 幅度及补偿激活的输出资源。系数测量、连续分段/IIR 与真实硬件资格验证留待后续。
