---
title: 图片证据的架构接入
description: 媒体、图片域、Artifact、Supervisor 与消费侧的责任边界。
---

# 图片证据的架构接入

实现对齐 [RFC #1972](https://github.com/oceanbase/powercontext/pull/1972) 的三表与 Artifact 方案。

| 边界 | 实现与复用 |
| --- | --- |
| 共享媒体 | `builtin/media` 类型契约与 `persistence/media.py`；资产、请求、容量三张表，不绑定宿主或 Memory 规则 |
| 图片域 | `builtin/images/application.py` 拥有校验、接收和 retry/reparse；`processing.py` 拥有模型调用及原子发布 |
| Artifact | `image_analysis.py` 注册 image-analysis 内容和草稿，复用既有版本、head、lineage、权限及精确读取 |
| Source | 注册原生 image.original/image.interpretation；保留 experimental 类型读取，不冒充完整 observation/current-head RFC |
| 调度 | 复用 ArtifactProcessingSupervisor 与共享 fencing/意图，图片独立 family 预算；只配 Vision 的子进程可运行 |
| 消费 | 确定性文字发布接入 Source-only Atomic Memory、Topic Memory 与 EvidenceResolver；原图 lineage-only，不可读解释审计-only |
| 事实排序 | Memory 逐陈述保存有界支持，区分 journal 消费顺序和原始输入顺序；精确授权读取并复核后发布 |
| 迁移 | 既有 registered domain bundle、shared maintenance lock、冻结 DDL、SQLite BackupProvider；计划与业务数据校验，不另建版本机制 |
| API | 五个新操作；Source/Artifact/capabilities 扩展既有读取家族；Python/JS/Desktop 从唯一 OpenAPI 生成 |
| 宿主和展示 | 正常 setup 与持久 Outbox 转换到新 capture 回执；Dashboard 组合授权后的通用读取，不另建领域状态 |

三表分别承担耐久 BLOB、逐输入请求状态、派生容量计数，未复制 Source／Artifact／lease／schema-version 职责。容量计数在接收中避免按全资产集合求和；启动完整核对仍有明确线性成本，实际测量见 [验证说明](integration-review.md)。

媒体协议支持稳定 processing_kind 与处理器类型化输入；未来音频等需注册自己的验证与发布规则，不能把图片 schema 或 worker 条件分支当成通用模态实现。首期不承诺音视频、非 SQLite 媒体存储、默认图片召回或 MCP 图片上传。
