# 课题续研台（TopicDesk）

面向学生、研究人员和项目协作者的跨会话课题交接工具。研究任务常跨越多个会话与工作日：背景、已确认结论、来源、当前进展和下一步散落在聊天记录与笔记里，重新开始时需要重复整理。课题续研台把这一切保存在 **PowerContext** 中——创建课题、记录有来源的笔记、整理会话交接；下次打开同一课题，所有内容从 PowerContext 读回，并提供续研包（有界历史上下文）与检索。

**PowerContext 在本项目中的职责（核心功能，非展示性接入）：**

| 产品概念 | PowerContext 能力 |
| :-- | :-- |
| 课题 | scope（数据分区与持久身份） |
| 结论 / 事实 / 问题 / 进展笔记 | `/v1/memory/remember` 显式记忆写入 |
| 笔记来源证据 | `/v1/sources/content` 采集 |
| 会话交接状态 | memory entry（kind=handoff） |
| 续研包 | `/v1/context/prepare` 有界历史上下文 |
| 关键词检索 | `/v1/memory/search`（全文检索模式） |

应用本身完全无状态：重启应用进程后，一切数据都从 PowerContext 读回，没有 localStorage、固定 JSON 或内存假数据。

## 环境前提

- Python 3.11+
- [uv](https://docs.astral.sh/uv/getting-started/installation/)（`curl -LsSf https://astral.sh/uv/install.sh | sh`）
- Linux / macOS（Windows 支持为 experimental，来自 PowerContext 本身限制）

依赖版本（由 `uv.lock` 锁定）：powercontext 1.4.1（含 cli、server extra）、fastapi、uvicorn、httpx。

## 安装与启动

```bash
cd qoder/topic-desk
uv sync                       # 创建虚拟环境并安装依赖

# 终端 1：启动 PowerContext Server（SQLite 落盘，无需外部数据库）
mkdir -p topic-desk-data
POWERCONTEXT_HOME=$PWD/topic-desk-data uv run powercontext server run --port 8010

# 终端 2：启动课题续研台
POWERCONTEXT_URL=http://127.0.0.1:8010 uv run uvicorn app.main:app --port 8020
```

打开 http://127.0.0.1:8020 。右上角显示「PowerContext 已连接」即启动成功。

> 配置项均可用环境变量覆盖：`POWERCONTEXT_URL`（Server 地址，默认 `http://127.0.0.1:8000`）、`POWERCONTEXT_TOKEN`（Server 启用鉴权时的 Bearer token，默认为空）、`POWERCONTEXT_HOME`（PowerContext 数据目录，默认为系统用户数据目录）。示例中使用占位值，不包含任何真实凭据。
>
> 如需让局域网内其他设备访问应用，给 uvicorn 追加 `--host 0.0.0.0`；PowerContext Server 默认只监听 loopback，请先阅读其部署文档再开放远程访问。

## 停止

两个终端分别 `Ctrl+C` 即可。数据保存在 `topic-desk-data/powercontext.db`（SQLite），下次启动自动加载。

## 数据清理

- 清空全部课题数据：停止服务后删除 `topic-desk-data/` 目录。
- 只移除示例课题：示例课题标题带「[示例]」前缀，可通过 PowerContext API 按 scope 停用/删除（当前版本应用内未提供删除按钮）。

## 故障排查

| 症状 | 处理 |
| :-- | :-- |
| 右上角显示「PowerContext 不可用」 | 终端 1 的 server 未启动或端口不符；确认 `POWERCONTEXT_URL` 指向正确地址 |
| 保存笔记报 503 | 同上，应用无法连接 PowerContext；按页面错误提示启动 server |
| 保存笔记报 502 | PowerContext 返回错误，查看 server 终端日志；常见为请求体超限（单条笔记 ≤ 4000 字符） |
| 8010/8020 端口被占用 | 换端口启动，并同步更新 `POWERCONTEXT_URL` / 应用访问地址 |
| 续研包显示为空 | 课题还没有记录，或检索词与任何记录都不相关；先保存笔记或换关键词 |

## 验证样例（可复现）

前提：按上方步骤启动两个服务。以下 curl 均通过课题续研台 API 完成，数据真实写入 PowerContext。

```bash
APP=http://127.0.0.1:8020

# 1. 创建课题（= PowerContext scope）
SCOPE=$(curl -s -X POST $APP/api/topics -H 'Content-Type: application/json' \
  -d '{"title":"验证样例课题","summary":"验证跨会话持久化"}' | python3 -c 'import json,sys;print(json.load(sys.stdin)["scope_id"])')
echo "scope: $SCOPE"

# 2. 记录一条带来源的笔记（来源先写入 /v1/sources/content，再写入 memory）
curl -s -X POST $APP/api/topics/$SCOPE/notes -H 'Content-Type: application/json' -d '{
  "kind":"conclusion",
  "text":"验证样例结论：数据必须跨会话持久化。",
  "source":{"title":"验证来源","url":"https://example.org","excerpt":"Sample evidence excerpt."}
}'

# 3. 保存会话交接
curl -s -X POST $APP/api/topics/$SCOPE/handoff -H 'Content-Type: application/json' -d '{
  "goal":"完成验证","done":["已写入样例"],"open_questions":["无"],"next_steps":["重启验证读回"]
}'

# 4. 模拟“结束会话”：重启应用进程后，从 PowerContext 读回
curl -s $APP/api/topics/$SCOPE | python3 -m json.tool    # notes 中应包含上面两条
curl -s "$APP/api/topics/$SCOPE/search?q=持久化"          # 检索命中样例结论
curl -s "$APP/api/topics/$SCOPE/context"                  # 续研包含历史上下文

# 5. 也可绕过应用，直接向 PowerContext 核对持久化（体现真实写入）
curl -s "http://127.0.0.1:8010/v1/scopes/$SCOPE/sources"  # 来源证据确实在 PowerContext 中
```

**预期结果**：步骤 4 中 notes 列表包含步骤 2/3 写入的条目（应用重启后仍存在，证明持久化由 PowerContext 承担）；检索返回带相关度分数的命中；续研包返回非空历史上下文；步骤 5 的响应包含来源摘录原文。

浏览器验证流程：打开 http://127.0.0.1:8020 → 「载入示例课题」→ 打开课题查看记录与续研包 → 「＋ 新建课题」→ 记录带来源的笔记 → 保存交接 → 重启应用进程并刷新页面 → 打开同一课题确认全部内容读回 → 在检索框输入关键词（如「BM25」）。

## 一键载入示例课题

页面上的「载入示例课题」按钮会创建一个标注 `[示例]` 的演示课题（混合检索实验续研），包含事实/结论/问题/进展四类笔记、一条论文来源和一份交接状态。**示例数据同样经 PowerContext 真实写入**——写入后可直接在本页检索、读取续研包，或用上面步骤 5 的方式向 PowerContext 直接核对。重复点击不会重复创建（幂等）。

## 测试

```bash
uv run pytest          # 10 个测试全部通过
uv run ruff check .    # 静态检查
```

测试分两类，互相区分：

- `tests/test_e2e_persistence.py`：**真实后端验收**。测试自身启动真实 PowerContext Server 子进程（独立 SQLite 数据目录），验证「会话 A 写入 → 应用关闭 → 会话 B 从 PowerContext 读回并可检索」闭环、示例数据幂等、来源证据真实落库（绕过应用直接查 PowerContext）。
- `tests/test_unavailable.py`：**本地错误处理单测**。验证 PowerContext 不可达时用户看到包含恢复步骤的明确错误（503 + powercontext_unavailable），以及后端 4xx/5xx 与连接失败被区分为不同错误码。不涉及真实后端。

## 目录结构

```
qoder/topic-desk/
├── app/
│   ├── main.py        # FastAPI 后端：所有持久化经 PowerContext HTTP API
│   └── pc.py          # PowerContext 轻量客户端与错误类型
├── static/            # 前端单页（原生 HTML/CSS/JS，无构建步骤）
├── tests/             # pytest：真实 e2e + 错误处理单测
├── site/              # Qoder Sites 展示网站源文件与截图
├── DEVELOPMENT.md     # Qoder 开发过程记录
└── SUBMISSION.md      # 参赛提交清单
```

## 已知限制

- 检索使用 PowerContext 的全文（text）检索模式；语义/向量检索需要为其配置 vector-search 与推理服务（可选能力，本验证未启用）。
- 应用暂不提供课题删除界面；数据清理见上节。
- 单实例单用户设计；多租户访问控制由 PowerContext Server 鉴权承担（本演示未启用）。
