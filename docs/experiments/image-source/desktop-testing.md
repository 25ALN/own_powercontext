---
title: Codex 图片功能本地验收
description: 用标准配置和启动流程验证桌面附件、图片材料、Memory 及新会话召回。
---

# Codex 图片功能本地验收

图片功能已接入普通 config init、setup codex、doctor codex 和 Dashboard，默认关闭。首次授权后，setup 自动管理客户端采集器；日常只需正常运行 Server 和 Codex，无须手动启动采集脚本或维护多份实验 env。

本机源码目录为 D:/code/git-project/powermem/powermem-image-source-experiment，唯一运行配置是根目录 .env，Server 地址为 http://127.0.0.1:18171。原有隔离数据库、资产目录、Scope 和历史材料继续使用。密钥保存在忽略文件。

## 1. 运行源码

首次安装依赖：

~~~powershell
cd D:/code/git-project/powermem/powermem-image-source-experiment
uv sync --locked --extra server --extra cli
~~~

日常启动：

~~~powershell
uv run --no-sync powercontext server run --env-file .env
~~~

保持终端运行；若 18171 已有健康的实验服务，不重复启动。常规 server extra 已包含图片解码依赖，images extra 保留为兼容入口。本地环回连接不再需要额外设置 NO_PROXY。

新用户运行 powercontext config init --output .env，在普通向导的可选图片步骤启用功能、选择模型连接，并授权当前项目的 Codex 图片采集。向导只写配置，不启动服务或调用模型。

## 2. 配置

| 配置 | 含义 |
| --- | --- |
| POWERCONTEXT_SERVER_IMAGES_ENABLED=true | 服务接收及后台解析，默认 false |
| POWERCONTEXT_CODEX_CAPTURE_IMAGES=true | setup 时授权当前项目采集，默认 false；运行中使用保存的授权 |
| POWERCONTEXT_SERVER_IMAGES_ASSET_DIRECTORY | 原图目录；省略时新库用数据目录，已有库采用记录的原目录 |
| POWERCONTEXT_SERVER_INFERENCE_VISION_USE_GENERATION=true | 显式复用 PowerContext 的普通生成模型、地址和凭据；必须支持 OpenAI Chat 多模态输入 |
| POWERCONTEXT_SERVER_INFERENCE_VISION_USE_GENERATION=false | 使用独立 Vision 配置 |
| POWERCONTEXT_CLIENT_SERVER_URL | setup 连接地址；本机为 18171 |

本机使用独立 MiniCPM-V-4.7-35B-A3B Vision、真实 DeepSeek 普通生成和 BGE-M3 embedding。普通模型不会因为开启图片就被认定为支持视觉。Codex 聊天的模型或订阅也不能代替 PowerContext 的模型连接。

复用生成连接时不需要第二份 API Key；仍需验证实际多模态能力，可执行一次有费用的合成图探测，不创建 Source：

~~~powershell
uv run --no-sync powercontext image probe --env-file .env
~~~

Memory 继续使用已有普通模型及处理策略。图片功能不修改 Artifact families、Topic 或调度；一图可能生成零条、一条或多条 Memory。

## 3. 一次安装 Codex 接入

Server 就绪后，在另一终端执行：

~~~powershell
cd D:/code/git-project/powermem/powermem-image-source-experiment
uv run --no-sync powercontext setup codex --source ./integrations/codex
uv run --no-sync powercontext doctor codex --json
~~~

--source 是 worktree 源码开发入口；合入并发布后使用常规 powercontext setup codex。源码的本地插件身份为 powercontext@powercontext-local，正式安装继续使用原 PowerContext 插件，不增加独立图片插件。

setup 保存当前项目采集授权，自动管理客户端采集器。Windows 使用当前用户 Startup 项及监督进程，无需管理员权限；SessionStart 能恢复监督进程，子采集器退出后会重启。它不管理 Server 服务。新项目需独立授权，重复 setup 不导入旧附件。

更新源码或插件后重新执行同一个 setup 命令，自动替换属于此客户端的旧后台进程并加载新代码；原暂存队列保留，不需要手工结束 Python 进程。

重启 Codex，在插件设置中启用本地 PowerContext，审阅并允许新的 Hooks。Codex 按具体定义要求信任，更新后可能再次提示。本轮 Interrupt 超时调整为三秒，如出现审阅提示需要允许。这是宿主权限机制，插件无法代替用户批准。

doctor 的 images 项应为 OK，显示采集器运行、启动项注册、当前 Scope 和服务图片状态。模型的网络能力以 probe 或实际任务为准。关闭状态明确显示采集关闭。

## 4. 在桌面端发送图片

在 Codex 中选择实验 worktree 项目，新建聊天，拖入或粘贴 [复杂验收图](fixtures/codex-complex-acceptance.png)，发送：

> 这是 CEDAR-58 项目的测试运行手册，请帮我整理当前有效规则、明确已经废弃的规则和待确认信息。以后讨论这个项目时需要用到这些规则。

不要在提示词抄写图中数值，避免文字 Source 提前提供答案。先测单 PNG，再分别测粘贴、JPEG、两图和中断，分别记录结果。

采集只读取已授权项目的新用户附件，按真实会话／项目 Scope 发送；已暂存图片继续投递到当时的 Scope。关闭期间和授权前的图片不补采。

当前限制：PNG/JPEG，每图最多 8 MiB、20 百万像素、边长最多 16384；每轮最多四张。图中的“72 MiB、三张”等是虚构项目业务规则，不是 PowerContext 限制。

自动适配限已验证记录格式：CLI exec 0.141.0、0.162.0-alpha.2、0.162.0-alpha.17.2；Desktop 内核 0.162.0-alpha.2 和 0.162.0-alpha.17.2。未知版本会拒绝并诊断；交互 CLI TUI 未开放自动适配。

## 5. 验证图片 Source

打开 [图片材料](http://127.0.0.1:18171/dashboard/images?scope=scp_15snkw9d13fh5b4gj63hze3xke&lang=zh)，或点击 Dashboard 侧边栏“图片材料”；确认 Scope 与 doctor 相同。

应出现新缩略图／任务。点开检查原图、解析状态、OCR、模型和附件来源。succeeded 表示解析 Source 已保存；pending／running 可刷新等待，失败看错误码及尝试记录。旧任务日期未知是兼容历史记录的正常展示。

/dashboard/image-task/{task_id} 是图片功能新增的详情页，可以从列表进入；无需先知道 ID，也无需先生成 Memory。

CLI 诊断：

~~~powershell
uv run --no-sync powercontext --server-url http://127.0.0.1:18171 --json image status --scope-id scp_15snkw9d13fh5b4gj63hze3xke
uv run --no-sync powercontext --server-url http://127.0.0.1:18171 --json image list --scope-id scp_15snkw9d13fh5b4gj63hze3xke
~~~

通用 CLI 数据操作的 endpoint 与 Server 的 --env-file 分开，此处明确指定 18171，避免误查默认 8000。插件采集使用 setup 保存的连接。

## 6. 验证 Memory 和新会话

本机 Memory 后台周期为 30 秒，真实模型处理还需要时间。进入 [Memory](http://127.0.0.1:18171/dashboard/notes?scope=scp_15snkw9d13fh5b4gj63hze3xke&lang=zh)，查 CEDAR-58／CEDAR58-TEST-G2。展开“原始材料”，点击图片解析来源，应看到对应历史原图和 OCR。

同一项目、同一 Scope 新建聊天，不附图、不引用旧聊天，发送：

> 先搜索 PowerContext 记忆，再告诉我 CEDAR-58 的图片大小规则、E731 的等待和重试规则、备份时间与时区。区分已经废弃的规则和待确认推测，并提供来源引用。

按 [使用案例与答案](complex-test-case.md) 核对。先显式要求搜索验证工具链；再另开聊天普通提问，验证 Hook 自动召回。后端命中不能代替桌面实际使用，必须检查工具结果／上下文、正确答案和引用。

Source 有数据而没有 Memory 时，检查解析可读性、普通生成模型、处理 family、积压和日志，不通过手写答案替代自动提炼验收。

## 7. 排查与关闭

| 现象 | 检查 |
| --- | --- |
| Hook 成功，没有图 | doctor 的授权、采集器、版本、Scope 和 endpoint；Hook 成功只表示命令完成 |
| 图已保存，解析失败 | 任务错误码、模型、鉴权和真实多模态请求 |
| 解析成功，没有 Memory | 普通生成模型、现有处理配置及实际候选 |
| Memory 存在，新会话未使用 | 同 Scope、MCP、实际搜索／自动注入和答案引用 |
| 改 .env 为 false 后仍采集 | .env 改动不撤销持久化授权，执行 no-capture setup |

关闭当前项目：

~~~powershell
uv run --no-sync powercontext setup codex --source ./integrations/codex --no-capture-images
~~~

最后一个授权项目关闭后移除自己的启动项并停止采集器；保留 Source、原图和回执。卸载插件前先关闭授权。仅在 Codex 中禁用插件会使监督进程退出，但不能代替正式撤销授权。关闭 Server 图片功能仍允许历史材料读取。

跨平台启动项代码已实现，原生执行本轮只验证 Windows。OceanBase、seekdb、其他 Agent、PDF／视频和以图搜图不在当前图片实验范围。
