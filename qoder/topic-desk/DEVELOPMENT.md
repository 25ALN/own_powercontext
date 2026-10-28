# Qoder 开发记录

本文记录课题续研台的开发过程。项目的需求拆解、方案设计、编码、调试和验证均在 Qoder（桌面端 Agent 会话）中完成，由 Qoder 生成代码、测试与文档，人工确认方向与关键决策。

## 1. 需求拆解

从赛题（Build with Qoder——用 Qoder 做一个让学习、科研和工作更高效的项目）出发，选定痛点：**科研/课程课题跨会话续研**——笔记散落在聊天记录和文档里，换台电脑、隔一周再回来，上下文就断了。

Qoder 将需求拆解为：

1. 领域模型映射：课题 = PowerContext scope，笔记/结论/问题/进展/交接 = memory entry，来源证据 = content source，续研包 = `context/prepare`，检索 = `memory/search`。
2. 最小闭环流程：新建课题 → 记录带来源的笔记 → 整理交接状态 → 关闭会话（重启应用）→ 重新打开课题读回全部内容 → 关键词检索。
3. 硬约束：应用本身无状态，所有持久化必须经 PowerContext 完成（禁止 localStorage / 固定 JSON / 假数据）；PowerContext 不可用时给出清晰错误；测试必须包含「保存后经真实读路径读回」的端到端用例。

## 2. 接口调研

开发前先在本仓库中核对了 PowerContext 的真实 HTTP 契约（`openapi/` 与已安装的 1.4.1 包），确认了以下关键行为：

- `POST /v1/scopes` 支持 `external_references`，`GET /v1/scopes` 支持 `external_reference_kind` 过滤——用于把「课题」与其他 scope 区分开。
- `POST /v1/memory/remember` 返回 mutation 记录，entry id 在 `records[0].artifact.artifact_id`。
- `POST /v1/memory/search` 的命中结构为 `hits[].memory.{text, artifact}`。
- `POST /v1/context/prepare` 按 query 相关性选取证据，不支持空 query。
- `POST /v1/sources/content` 采集来源，`GET /v1/scopes/{id}/sources` 可核对落盘结果。

所有调用都只使用上述真实存在的接口，没有虚构 API。

## 3. 关键调试过程

以下问题均由 Qoder 定位并修复：

| 问题 | 根因 | 修复 |
| --- | --- | --- |
| 创建课题返回 404 scope_not_found | `context_references` 语义是「引用已存在的 scope」，不是标签 | 改用 `external_references` + 列表过滤标记课题 |
| 检索结果 text/entry_id 为 null | 1.4.1 返回结构为 `hit.memory.*` 而非顶层字段 | 修正响应映射 |
| 写入带来源的笔记时 500「Event loop is closed」 | 复用的 httpx.AsyncClient 绑定到了已关闭的事件循环（TestClient 每个请求使用独立 loop） | 改为每次请求使用短生命周期 client |
| 界面显示「版本 null」 | entry 的版本号在 `artifact.revision` 而非顶层 `version` | 修正渲染字段 |
| 续研包状态为 empty | `context/prepare` 基于 query 相关性选证据，空 query 不被支持 | 未指定 query 时，取最近一条交接记录（或首条记录）前 200 字作为默认 query |
| 检索结果里出现 `[[source:...]]` 原文 | 来源标记内嵌在笔记正文中 | 前端渲染前剥离标记 |

## 4. 实现与验证阶段

1. **后端**：FastAPI 应用 + PowerContext HTTP 客户端封装（`app/pc.py`、`app/main.py`）。异常处理区分「服务不可达（503）」与「服务返回错误（502）」。
2. **前端**：单页应用（`static/`），覆盖课题列表/空态/加载态/错误态、笔记来源折叠、续研包展示、检索。
3. **测试**（`tests/`，10 个用例全部通过）：
   - `test_e2e_persistence.py`：**启动真实 PowerContext Server 子进程**（独立临时数据目录），走完「写入 → 重启应用 → 读回 → 检索 → 续研包」全流程，并直接调用 `/v1/scopes/{id}/sources` 核对来源真实落盘。示例课题一键载入的幂等性也在真实服务上验证。
   - `test_unavailable.py`：PowerContext 不可达时返回 503 与明确恢复指引；后端错误返回 502 并保留区分。
4. **人工浏览器验证**：本地启动真实 PowerContext Server 与应用，在浏览器中完成「新建课题 → 记录带来源笔记 → 保存交接 → 重启应用进程 → 重新打开课题（数据全部读回）→ 检索命中」的完整闭环，并截图存档（`site/screenshots/`）。
5. **代码质量**：`ruff check` 与 `ruff format` 通过。

## 5. 诚实性说明

- 端到端测试连接的是**真实 PowerContext Server 子进程**（`powercontext server run`），不是 mock；文档中标注了哪些验证是真实服务、哪些是单元级错误路径模拟。
- 项目尚未交予外部真实用户使用，提交材料中的使用效果部分提供空白模板，不编造使用数据。
