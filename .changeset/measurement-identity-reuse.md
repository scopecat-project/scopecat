---
default: patch
---

复用不可变测量 header/schema 实例的规范身份，避免显式大扫描逐点重复序列化与哈希。
保留服务端独立身份校验、持久格式及批次策略，并冻结 entity index 中未经验证副本带入的 metadata。
