---
title: 图片解释、重试与记忆
description: Supervisor 调度、精确 Artifact 历史与原始证据顺序。
---

# 图片解释、重试与记忆

普通 Server 启动既有 Artifact Processing Supervisor。图片使用 `image-interpretation` binding、`image-analysis` family 与独立预算，不启动第二套守护进程。仅配置 Vision 也可解释图片；普通生成模型仍决定后续 Memory。

worker 在短事务内取得带 fencing 的 claim 并提交一次尝试，再进行 HTTP。响应未知占用预算；超时、429 或进程中断按固定预算恢复，明确 provider 拒绝则终止。没有隐式 SDK 重试。发送和发布前检查 Scope/Source/Artifact 权限及精确状态；旧 worker 或被替换请求的晚到结果不发布。

成功发布在同一事务保存 image-analysis Artifact 乐观版本、确定性 image.interpretation Source、结果引用、终态与容量释放。解释输出包括可见文字、关系、不确定项和可读性，不能当作已批准决策。原图只作 lineage，不可读结果不进入自动或显式文字抽取。

```sh
powercontext --server-url http://127.0.0.1:8000 --json image status
powercontext --server-url http://127.0.0.1:8000 --json image list --scope-id <Scope>
powercontext --server-url http://127.0.0.1:8000 --json image retry <failed_request_id> --scope-id <Scope> --original-source-id <原图 Source ID> --retry-key retry-1 --expected-state-version <版本> --expected-artifact-revision <当前解释版本或 0> --expected-profile-digest <失败请求配置摘要>
powercontext --server-url http://127.0.0.1:8000 --json image reparse <原图 Source ID> --scope-id <Scope> --reparse-key parse-2 --expected-artifact-revision <当前解释版本或 0> --expected-profile-digest <当前配置摘要>
```

替换活跃请求还需传 `--request-id` 和 `--expected-state-version`；先按精确请求查询当前状态。retry 固定旧失败配置，reparse 固定当前配置。重复同一操作键返回首次回执，配置改变不会重新定义它。status 读取 capabilities，不另设图片任务诊断 API，也不调用模型。

Memory 消费发布位置以推进游标，同时保留原图位置作为事实依据。旧图位置 1、更新文字位置 2、解释发布位置 3 时，旧图不覆盖新文字；reparse 同样保留原始锚点。逐陈述证据与日期随 Memory 内容保存，未知历史冲突保守保留当前值，独立事实可以前进。算法及兼容说明见 [契约](contracts.md)。

解释列表、资产、Source、Artifact 读取和 Dashboard 均无模型调用。精确历史引用在后续 reparse 后仍指向旧版本，不跳转当前 head。关闭图片接收与处理后仍按既有权限读取；不自动回退到另一个模型 endpoint。
