# 实际验证记录

整理日期：2026-10-29（UTC+8）。证据来自用户上传的八张原始 Dify 节点追踪截图，图片保持原始字节。关键字段另转录到 `observations.json`，便于与截图核对；该 JSON 不是另外运行接口生成的日志。

## 1. 本次配置与用户

应用系统 ID 为 `30c311d3-33a5-4c2c-94e6-a63f30c4a10a`，命名空间为 `nickname-final-20261029-01`。A 和 B 使用同一应用及命名空间。

| 项目 | 用户 A | 用户 B |
| --- | --- | --- |
| Dify user_id | `0a2c80e4-22a5-4ca6-99cf-71024eed2821` | `d47f096d-1c2f-4a19-ba57-c90dbed73690` |
| Scope | `scp_0khyfwceht79bwd8psd7cazq3f` | `scp_5xh4wcn7w3nawg6zm22axh1e8d` |
| user_key 前缀 | `5ecfa0c982215bcb` | `56662e4f96c1f979` |
| 首次检索 | HTTP 200，`hits: []` | HTTP 200，`hits: []` |

两人的完整哈希均按提交 DSL 的 `[namespace, app_id, user_id]` 算法重新计算并与截图一致。Scope 使用服务端真实返回值，没有从哈希拼出 Scope ID。

## 2. A 首轮：空历史后真实写入

输入：

```text
我把周报文件命名为“周报_海盐柠檬1029”，结果发错了群。
```

[用户与 Scope](a1-user-scope.png) 展示 A 的系统用户输入、稳定创建键，以及创建响应 HTTP 201。

[首次检索](a1-empty-search.png) 展示相同 Scope 的搜索请求及响应：

```json
{"mode":"text","hits":[]}
```

响应状态为 HTTP 200，响应头时间为 `2026-10-29 05:35:08 GMT`。

[首轮写入](a1-save.png) 展示 `POST /v1/memory/remember` 的同一 Scope 请求和 HTTP 200 响应，`changed` 为 true，`records` 非空。写入结果的真实身份为：

```json
{
  "family": "atomic-memory",
  "artifact_id": "art_76d4e19fcee7454d8cc343722e6c6b3f",
  "revision": 1
}
```

保存正文包含输入的“周报_海盐柠檬1029”，以及实际生成结果：

```text
称号：海盐柠檬社死特调
判词：周报名起得比奶茶还讲究，却精准投进了最不该进的群，这杯“社死特调”先干为敬。
```

写入响应头时间为 `2026-10-29 05:35:10 GMT`。这些信息证明首轮从空检索开始，随后把真实互动写入，而非仅检索预置故事。

## 3. A 后续对话：召回同一条记录

用户按测试步骤在同一浏览器开启新对话，并提供后续运行追踪。其提交的 `user_key` 和 Scope 与首轮一致。

[后续检索](a2-recall.png) 展示同一 Scope 的搜索请求、HTTP 200 以及非空 `hits`。命中身份为：

```json
{
  "family": "atomic-memory",
  "artifact_id": "art_76d4e19fcee7454d8cc343722e6c6b3f",
  "revision": 1
}
```

这与首轮 `records[].artifact` 的身份完全一致，正文也包含同一段周报经历及“海盐柠檬社死特调”。响应头时间为 `2026-10-29 05:47:42 GMT`，晚于首轮写入。

[整理历史](a2-history.png) 展示该记录已被整理为非空 `history`；DSL 的 USER 消息引用该输出。

本组没有另行转录会话 ID，开启新对话的动作依据用户操作记录；可直接由截图核验的关系为同 Scope、同 Artifact ID/Revision、同正文的先写入后召回。A 第二轮最终回复和第二轮 remember 原始响应未包含在这两张截图中，不将其列为已截图核实的项目。

## 4. B 首轮：独立用户没有读取 A 历史

用户通过独立无痕窗口访问公开 Web App 测试 B。

- [用户输入与创建键](b-user.png)：`user_id` 与 A 不同，`app_id`、namespace 与 A 相同，生成不同的 user_key。
- [独立 Scope 的空检索](b-empty-search.png)：实际 Scope 为 `scp_5xh4wcn7w3nawg6zm22axh1e8d`，与 A 不同。HTTP 200，响应为 `{"mode":"text","hits":[]}`，响应头时间为 `2026-10-29 05:57:30 GMT`。
- [B 实际回复](b-reply.png)：称号为“咖啡泡面双修侠”，包含“记忆已保存，下次新会话仍可读取。”提示，没有引用 A 的周报经历。

B 输入按本次操作步骤为“我把咖啡倒进了泡面，吃完才发现。”；提供的三张节点图没有单独展示完整用户 query，也没有 B remember 的原始响应。上述结论区分实际截图信息与用户测试步骤，不将提示文本替代写入原始响应。

这组证据支持按 Dify 用户分配独立 Scope 的应用层隔离，不等同于验证完整的服务端多租户权限体系。

## 5. 导出检查及整理修正

新导出包中三个 HTTP 节点的 URL 都引用 `PC_BASE_URL`，鉴权都使用 Bearer 和 Secret 变量 `PC_TOKEN`。该变量导出值为空；未发现导出的模型 API Key。正式工作流的读写 Scope 都引用解析节点，且不存在固定玩家 Scope 环境变量。

整理提交版本时做了两处修正：

1. 原“保存检查”的 `status_code` 错误引用读取历史，已改为引用保存记忆的状态码；原图中两者均为 200，首轮原始写入证据仍可直接核对。
2. 原 LLM 使用内置 memory 的当前消息模板，整理版将相同模板移到显式 USER 消息并将 memory 设为 null，明确使用 PowerContext 提供历史。

截图保持导出前真实运行记录；上述整理修正另通过本地静态检查和代码节点/变量渲染模拟验证，详情见 `package-checks.json`。没有把模拟接口结果冒充新的 Dify 实机运行。

## 6. 复现

导入提交版后填写服务地址、Secret 令牌及未使用过的 namespace，按 README 顺序执行 A 首轮、A 新对话、B 首轮。整组测试保持 namespace 不变。工作流自动创建或复用用户空间，不需要导入这些证据 ID，也不需要用辅助脚本预置故事。
