---
title: 图片存储、迁移与备份
description: 可选解码、共享 SQLite 媒体存储、显式迁移与恢复。
---

# 图片存储、迁移与备份

安装 `powercontext[server,cli,images]`；`images` 提供可选 Pillow 解码器，普通 server/builtin 安装不要求 Pillow。默认关闭，读取已有证据不要求视觉凭据；预览缩略图可在解码器缺失时省略。

在普通配置向导选择独立 Vision endpoint，或明确选择支持图片的普通生成连接。独立配置示例：

```dotenv
POWERCONTEXT_SERVER_DATABASE_URL=sqlite+aiosqlite:///D:/data/powercontext.db
POWERCONTEXT_SERVER_IMAGES_ENABLED=true
POWERCONTEXT_SERVER_INFERENCE_VISION_MODEL=<实际模型 ID>
POWERCONTEXT_SERVER_INFERENCE_VISION_BASE_URL=<实际 HTTP(S) endpoint>
POWERCONTEXT_SERVER_INFERENCE_VISION_DEPLOYMENT_ID=<部署标识>
```

非 loopback 需要 HTTPS。密钥保留在受保护配置中；接收只固定非秘密 profile，不探测模型。原图 BLOB 与其他业务记录保存在同一数据库，不需要资产目录。`IMAGES_ASSET_DIRECTORY` 是废弃的旧实验设置，不改变原生资产位置。

## 已有数据库

先关闭服务和所有写入者。计划不创建媒体表：

```sh
powercontext server db-migrate plan --bundle media --env-file .env
powercontext server db-migrate apply --bundle media --env-file .env --plan-id <计划 ID> --yes --maintenance-confirmed --backup auto
```

apply 使用既有共享维护锁、计划校验和、SQLite BackupProvider 和冻结 DDL。计划后 schema 或资源改变会要求重新计划；部分媒体表或未知 schema 不自动修复。已有文字 Source 身份、payload、journal 和 Artifact 不变。新业务库可以直接初始化；普通启动不偷偷升级已有文字库，图片未就绪不阻塞文字服务。

启用新的 processing ownership 时，再按 `powercontext server processing-migrate --help` 在停写窗口执行显式 processing migration；不要删除旧 manifest 来绕过 ownership 校验。停用只改图片开关并保留 profile 或显式 `image-analysis` family 声明，历史所有权不因临时缺凭据改变。

旧七表／文件部署会在迁移前拒绝，错误码 `experimental_media_import_unsupported`。保留原数据库及资产配对备份；不要删除旧表、手改 Source 名称、把 ia_ 改成 ma_，也不要宣称已经完成导入。旧实验 Definition 的文本历史读取仍保留，原生 reparse 不接收未转换的旧原图。

## 上传与精确读取

```sh
powercontext --server-url http://127.0.0.1:8000 --json image upload screenshot.png --scope-id <Scope> --capture-key screenshot-1 --context-text "本次报错"
powercontext --server-url http://127.0.0.1:8000 --json image request <request_id> --scope-id <Scope>
powercontext --server-url http://127.0.0.1:8000 image download <asset_id> --scope-id <Scope> --output restored.png
```

上传需要已有 Scope 的 contribute 与 Source read。相同上传使用稳定 capture_key 重放；新的采集使用新键。download 校验摘要和长度并原子创建新文件，不覆盖已有目标。请求查询返回精确原图、结果 Artifact 与发布 Source 引用；通过通用 Source/Artifact 接口读取内容。

## 备份与恢复

使用既有 SQLite BackupProvider 创建完整一致快照，再从备份恢复到独立位置并校验。BLOB、原图、请求、Artifact 历史、回执和容量位于同一快照，不再有文件目录配对问题。直接复制运行中带 WAL 的主文件不建立一致备份。

启动会分页核对所有资产摘要、请求和派生容量，成本为 O(保留字节数 + 请求数)。核对采用联合主键 seek，BLOB 批次最多 16 MiB；不是接收时的全库扫描。损坏数据报告错误并保留原记录，文字服务仍可用。

逻辑 portable bundle v1 尚不编码媒体 BLOB／请求，因此含图片 Scope 的 export/restore 在写入前拒绝；已有完整备份目标不会被不完整输出覆盖。普通文字及 Memory 排序元数据仍可逻辑复制。实际恢复、并发预算与故障测试范围见 [验证说明](integration-review.md)。
