---
title: 图片证据与共享媒体契约
description: 原图 Source、解释 Artifact、公共操作、事务、权限与事实排序。
---

# 图片证据与共享媒体契约

实现依据 [RFC #1972](https://github.com/oceanbase/powercontext/pull/1972)；HTTP 唯一来源是 [OpenAPI](../../../openapi/powercontext.yaml)。图片不是新增的 Memory 写入入口，解释由图片域发布，现有 Source-only 流程决定是否记住。

## 权威记录和共享存储

| 记录 | 职责 |
| --- | --- |
| `image.original` Source | 不可变上传身份、Scope、来源、固定上下文、资产描述及原始 journal 位置 |
| `image-analysis` Artifact | 每个原图的解释逻辑身份，乐观并发版本、固定配置、原始锚点、经校验输出和 lineage |
| `image.interpretation` Source | 每个精确解释版本的确定性文字快照、原图锚点及 ArtifactRef；读取不调用模型 |
| `pc_media_assets` | Scope 与 SHA-256 定位的 BLOB、长度、类型化元数据；同 Scope 去重，跨 Scope 独立授权 |
| `pc_media_processing_requests` | processing kind、操作身份、固定输入、不可变首次回执、当前状态、claim 和有界尝试 |
| `pc_media_capacity` | 可重建的全局及 Scope 字节数和活跃请求数，在接收事务内原子更新 |

仅新增这三张共享媒体表。没有私有 schema-version、任务尝试表、资产文件或独立 worker lease 表。解释复用既有 Artifact 内容、head、lineage、发布及权限表；调度复用既有 Supervisor 意图和 fencing。

## 五个公共操作

均为 POST JSON；已有通用 Source、Artifact 和 capabilities 接口承担其余读取。

| 路径 / operationId | 行为 |
| --- | --- |
| `/v1/images/capture` / `capture_image` | 单图接收，202 返回原图、资产、sequence、`request_id` 和重放标志 |
| `/v1/media/assets/get` / `get_media_asset` | 授权后校验摘要并返回精确字节 |
| `/v1/media/requests/get` / `get_media_request` | 按 Scope、request_id 返回固定输入引用、当前状态、有界尝试及精确发布引用 |
| `/v1/media/requests/list` / `list_media_requests` | 按 Scope、processing_kind、可选状态与有界游标分页，最多 100 条；不读取资产 BLOB |
| `/v1/images/analysis/request` / `request_image_analysis` | 显式 `retry` 或 `reparse`，统一请求模型和幂等回执 |

新客户端使用原生操作，不再调用七个 experimental 路径。通用 Source get/list 支持原生及旧实验图片类型；通用 Artifact 精确读取、元数据和历史支持 `image-analysis`，不把该 Family 加入通用写入、默认召回或候选治理。

## 接收与状态

接收先严格解码 PNG/JPEG，再在同一 SQLite 事务保存 BLOB、原图 Source、处理请求、容量、幂等回执及处理意图。失败回滚全部写入。202 表示耐久接收，不表示已调用模型。文件 SQLite 必须使用 WAL/DELETE journal 和 FULL 或更强同步。

相同 Scope、capture_key 和附件位置的相同内容重放返回首次不可变回执，即使配置或请求状态已经改变；不同内容返回 409。同字节不同采集保留不同原图 Source。新资产 ID 为 `ma_` 加原始字节 SHA-256；请求使用 `mr_` 身份，不把配置版本当作 processing kind。

图片 `processing_kind` 为 `image.interpretation`。每个 `(Scope, 原图 Source, processing_kind)` 最多一个活跃请求；pending、running、retry_wait、blocked 均计入活跃配额。succeeded、failed、superseded 为终态。配置不可用或停用时保留工作，不耗费新尝试。

retry 只针对 failed，要求精确 request_id、expected_state_version，复制旧配置与上下文。reparse 固定当前配置，要求 expected_artifact_revision（尚无结果时为 0）和 expected_profile_digest；替换活跃请求时还必须提供精确 request_id/state_version。替换原子标记旧请求 superseded，旧 claim 的晚到响应不能发布。操作 idempotency_key 重放返回首次创建的请求。

默认最多 3 次尝试，硬上限 5 次；每次先提交计费尝试，再进行一次 HTTP 调用，关闭 SDK 隐式重试。未知响应也占一次；失败重试和崩溃恢复不重置预算。单次超时默认 120 秒、硬上限 300 秒，输出最多 64 KiB。Artifact、新版本文字 Source、终态、配额释放及意图确认在同一事务提交。

## 权限与读取

capture、retry、reparse 需要 Scope contribute 和相应 Source read；引用已有上下文还要读该精确文字 Source。reparse 已有结果需要现有 image-analysis Artifact write。Scope 管理角色不隐含内容权限。worker 在发送和发布时复核身份、权限、fence 和 claim。

资产、请求和原图读取要求所属 Scope/source read。只分享 image-analysis Artifact 可以读取其解释，不授权读取原 Scope、原图、资产或请求。功能关闭、缺视觉凭据时历史 Source/Artifact/资产仍可按权限读取；读取和诊断不调用模型。

原图只作 lineage；readable/partial 解释可进入文字证据，不可读解释只作审计。相同资产或重新解析不增加独立证据数。历史证据始终沿精确 ArtifactRef 读取，不跳到当前 head。

## 迟到证据与 Memory

消费游标按发布位置推进，事实新旧按原始输入锚点比较。图片位置 1 的旧值 100，在文字位置 2 的新值 200 之后发布于位置 3，仍保留 200；再次解析保留位置 1，不制造新事实。

Memory 保存版本化、32 KiB 有界的逐陈述排序依据，与精确 kind/text 摘要绑定；每个陈述保留自己的证据和适用日期，不取整个 lineage 的最大或最小时间。模型只能选择给定证据 ID，不能构造锚点。明确事实日期优先；未知或不可比较的冲突保留当前记录，独立事实可以继续发布，不另建矛盾副本。

已有 Memory 直接使用保存的陈述关联。缺失元数据时，仅对一个授权的、精确文本一致的普通 Source 恢复已知顺序；其他情况为未知，不全库扫描、不回读当前 heads、不重新提炼。格式不支持、内容摘要损坏或锚点与保留证据不符时明确失败。修订、恢复、复制和备份保留排序元数据及历史身份。

## 预算与兼容

默认单图 8 MiB、HTTP body 12 MiB、20,000,000 像素、单边 16,384、最多四个独立附件；全局 1 GiB、1024 活跃请求，每 Scope 256。模型上下文有界并固定于接收时。容量与重放均在同一全局写入边界执行，不能并发超分配。

新库按冻结 DDL 初始化。已有文字库使用注册的 media migration bundle，先只读 plan，再停写、备份、显式 apply；部分表或未知形状拒绝自动修复。旧 `experimental.image.*` Definition 继续解码，不改身份、payload 或 journal。七表加资产目录的部署尚无合格转换器，迁移在写入前返回 `experimental_media_import_unsupported`，不能把加三表当作旧资产已导入。

原生完整 SQLite backup 包含 BLOB、请求和历史。逻辑归档 v1 无媒体字节编码：含图片的 Scope 在发布备份或 restore 写入前明确拒绝；普通 Memory 的排序元数据支持逻辑复制。具体运维步骤见 [迁移与备份](phase2-usage.md)，测量限制见 [验证范围](integration-review.md)。
