---
default: minor
---

新增单次完整 IQ shot 数组的 count 与复数 mean 公共计算，单比特 Ramsey 示例通过普通返回树显式选择 raw、stats 或 both。保留归约语义与历史结果；stats-only 不删除已有数据，也不能恢复分布。原生 NumPy compute 拒绝部分不可用数组，避免丢失掩码后把占位值当作有效样本。
