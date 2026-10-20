---
default: minor
---

# 同一记录执行 flux 校正与 IQ 读出

参考 list-mode target 可在同一 strict-grid 完整记录中合成零 DC flux 校正、IQ/readout 和 acquisition。保留原 IQ 样本及采集窗口，FIR 尾巴仅扩展记录末端，并检查合成输出资源、幅度和内存预算。预览与实际命令编码共用已冻结缓冲区；不扩展物理模型、IIR 或连续分段状态。
