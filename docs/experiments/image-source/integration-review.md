---
title: 图片证据验证与兼容范围
description: RFC 1972 对齐后的行为测试、容量成本和外部验收边界。
---

# 图片证据验证与兼容范围

## 架构反馈对应实现

[RFC #1972](https://github.com/oceanbase/powercontext/pull/1972) 已有审查认可记录，合并状态由该 RFC 的 GitHub 页面决定。本实现以其共享三表、解释 Artifact、既有 Supervisor 与原始证据顺序要求为依据，不把实现推送当作 RFC 合并或产品发布。

| 反馈 | 实现与证据 |
| --- | --- |
| 复用 Artifact 框架 | image-analysis Family 复用版本、head、lineage、精确读取与权限；原图 Source → 精确 Artifact → 确定性解释 Source → Memory |
| 减少新增表 | 仅共享资产、请求、容量三表；BLOB 与 Source/回执/意图原子接收，没有图片私有版本表、文件资产或第二套租约 |
| 复用调度 | 实际 ArtifactProcessingSupervisor 子进程与 loopback HTTP；Vision-only 配置、预算、429、明确失败、进程崩溃及晚到响应 |
| 迟到事实 | 实际 SQLite/子进程解释，受控 Memory 模型；同窗与跨窗旧图片及 reparse 不覆盖更新文字，消费游标仍推进 |
| 权限 | enforced HTTP：Scope 管理不隐含内容读取；Artifact 分享可读解释，但不能读取原图 Scope/资产/请求；禁止通用写入伪造图片 Source |
| 迁移 | 已有文字库只读 plan、显式 apply、完整 SQLite 备份恢复，保持原 Source 身份/payload/journal；部分 schema 和计划变更拒绝 |
| 兼容策略 | 旧七表／文件导入明确拒绝；实验 Definition 解码保留；逻辑归档 v1 含媒体提前拒绝，普通 Memory 排序元数据可复制 |

## 本地行为验证

环境为 Windows 11 AMD64、Python 3.14.5、SQLite 3.50.4。测试使用独立文件数据库、真实 HTTP 和真实 Supervisor spawn，不连接私有服务。

- `tests/e2e/test_media_storage.py`：接收重放、资产去重、故障回滚、并发配额、活动请求替换、冻结迁移、SQLite 备份与损坏拒绝。
- `tests/e2e/test_media_processing.py`：真实 child PID、HTTP 调用、OS 终止后的已花费未知尝试、租约恢复、429 三次预算、失败 retry 与旧 claim 失效。
- `tests/e2e/test_media_http.py`：五个原生操作、PNG/JPEG、参数与体积准入、权限矩阵、Artifact 分享和同键回执。
- `tests/e2e/test_media_memory_order.py` 与逐陈述单测：同窗/跨窗、重启、reparse、混合事实、未知依据、明确日期、逻辑复制及损坏锚点写入前拒绝。
- 现有图片 Evidence、Dashboard、Codex、CLI、配置向导、Memory、归档和生成契约回归继续覆盖公共行为。实际执行结果与命令保存在 [实施记录](implementation-log.md)。

完整 SQLite backup 是媒体恢复路径；含媒体 Scope 不支持 portable bundle v1。这是明确的格式边界，不能把普通文字的逻辑复制测试描述成媒体导出验收。

## 容量与锁成本

可复现脚本为 `evaluation/media_capacity.py`，原始结果见 [media-capacity-results.json](media-capacity-results.json)。比较生产容量计数方案与同一 SQLite 写事务内 SUM/COUNT 聚合方案，均经过真实 PNG 接收和耐久提交。896 个活跃请求起步，128 个样本补至 1024 上限，再验证八个新请求拒绝；聚合比较中的锁超时仅在计时批次后顺序补齐，以核对相同容量边界。

| 保留资产 | 方案 | 并发 | 接收 p95 ms | 已接受 / 测量批次 | 锁超时 |
| --- | --- | --- | --- | --- | --- |
| 8 个、600 B | 容量计数 | 1 | 30.087 | 128/128 | 0 |
| 8 个、600 B | 容量计数 | 8 | 1372.21 | 128/128 | 0 |
| 8 个、600 B | 聚合 | 1 | 26.984 | 128/128 | 0 |
| 8 个、600 B | 聚合 | 8 | 1013.905 | 128/128 | 0 |
| 131080 个、1 GiB | 容量计数 | 1 | 55.857 | 128/128 | 0 |
| 131080 个、1 GiB | 容量计数 | 8 | 1422.994 | 128/128 | 0 |
| 131080 个、1 GiB | 聚合 | 1 | 127.111 | 128/128 | 0 |
| 131080 个、1 GiB | 聚合 | 8 | 2158.851 | 115/128 | 13 |

活跃 Scope 和 Scope 内 due 查询通过 `processing_kind/active_key/scope_id` 覆盖索引检索，EXPLAIN 记录在结果中，避免按终态请求历史枚举。小数据上计数行有额外 SQL 开销；大量小资产时全表 SUM 扩大持锁时间。计数方案并未消除 SQLite 单写者竞争，八路时 p95 仍超过一秒。报告包含锁获取调用时间、从获取完成到提交返回的持锁下界、SQL 条数和 EXPLAIN；聚合逻辑输入为所有资产与当时活跃请求，不把它当作精确 VM 扫描行数或物理 I/O。

两个方案均使用生产完整启动核对。1 GiB/131080 资产与 896 请求核对为 10.2–40.1 秒（较慢一例与静态网站构建并行）；这是 O(保留字节数 + 请求数) 的恢复成本，不是常数时间启动。批次按联合主键 seek，单批 BLOB 最多 16 MiB。历史压力 BLOB 只验证存储完整性，不是模型或解码 fixture。

另行使用真实 1536×1536 随机 PNG，每张约 7.09 MB，测量八次新 BLOB 写入。顺序接收 p95 176.065 ms，持锁下界 p95 82.331 ms；四路接收 p95 413.297 ms，持锁下界 p95 118.171 ms。解码发生在写事务外。少量样本、Windows 本地文件系统与同时运行的文档构建、其他检查限制这些测量，不据此承诺其他部署延迟。

## 外部验收边界

loopback provider 返回受控结构化内容，Memory 模型由测试注入；它们验证协议、顺序和事务，不评价外部视觉模型质量。真实子进程、OS 终止与实际 SQLite backup 已执行；外部生产服务、真实 Codex Desktop 拖图/粘贴、全新聊天自动召回和 macOS/Linux 采集器生命周期没有在本次实现中重新验收。历史探测材料不替代当前代码的验收。Desktop 原生协议资格绑定实际 wheel 和合同摘要，不推导图片 UI 资格。
