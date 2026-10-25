---
title: 图片采集授权与查看
description: 正常 setup、宿主表示、持久 Outbox 和精确证据展示。
---

# 图片采集授权与查看

普通向导默认关闭图片识别，并说明图片与上下文会发往哪个模型服务。解码依赖单独安装 `images` extra。正常 Codex setup 根据 `POWERCONTEXT_CODEX_CAPTURE_IMAGES` 持久管理当前项目、Scope 和 Server 目的地的授权与采集器生命周期，不要求手工运行图片脚本。Server 开关不等于读取宿主历史的许可。

采集只跟随已授权项目和已提交消息，保留 host/session/turn、message、附件位置和实际表示。`host_image` 表示宿主提交的图片块，可能已经缩放，不能宣称是磁盘原文件。没有证据的版本、TUI 或附件形式不自动开放。关闭停止新收集；重新开启可投递已暂存且重新授权的材料，不补采关闭期间的历史。

客户端 Outbox 保留有界原字节及稳定接收键，投递前复核当前授权，调用 `/v1/images/capture`，仅在耐久回执后确认。内部旧 `task_id` checkpoint 列为兼容读取保留，新值保存 `request_id`；它不代表服务端仍存在图片专用 task API。旧已确认回执不重新发送。

Dashboard 图片入口按 Scope 分页查询共享请求，通过原图 Source、精确 ArtifactRef 与资产接口组成展示。列表不批量加载 BLOB；缩略图与下载分别授权。Artifact 分享允许查看解释，不能借此读原图 Scope 或资产。HTML 文本转义，历史证据读取精确发布版本。

真实宿主验证需固定 Codex 版本、入口、图片表示、项目与 Scope，分别检查接收、解释、Memory、新会话实际召回。受控 Hook、直接脚本或 provider 测试不能替代真实 Desktop UI 及全新聊天验收。本实现的实际执行范围见 [验证说明](integration-review.md)。
