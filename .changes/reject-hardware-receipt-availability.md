---
type: fix
---

硬件回执明确拒绝无法保留采样诊断的数组

带 availability 的 MeasurementArray（包括 partitioned array）不再通过 hardware receipt v2 静默丢失 validity/diagnostics，避免消费者把缺失采样的填充零当作真实测量。collect 与 run hardware endpoint 返回明确的 HTTP 500，UI/API 消费者收到错误；采集可能已执行，此错误不代表命令未执行。

当前仍不支持通过该协议传输部分或全部不可用的数组；完整数组和整项 MeasurementUnavailable 保持原行为。未扩大 wire 格式，也未改动已有 v2 framing 适配。
