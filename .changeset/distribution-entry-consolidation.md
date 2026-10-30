---
default: minor
---

# 收敛发行与开发测试入口

数字发行只公开 macOS/Windows 安装包、四个作者与执行用 wheel，以及 format 2 `release.json`；app、tutorials、testkit、UI ZIP 和 preview 清单保留为内部候选材料。开发及集成测试使用显式源码或应用 runtime，退役通用 `install.py` 安装入口与持久 SHA preview 发布路径。

Windows 覆盖安装会替换完整应用 runtime，清除旧模块与字节码；遇到文件占用、目录链接或未知 resources 目录时拒绝安装。用户工作区和外部执行环境保留。应用打包时预编译可搬迁的 checked-hash 字节码，启动时不回写应用目录。
