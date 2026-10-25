---
title: Codex 图片附件与采集实测证据
description: 固定 CLI、真实 Desktop 附件、Hook 时序、Desktop 内核采集和真实 Vision 的证据及限制。
---

# Codex 图片附件与采集实测证据

当前证据支持 Codex 桌面自动图片 Source 的基础链路：用户实际桌面会话的三张 PNG 已保存原图与解析，真实 MiniCPM-V 35B 全部成功，资产回读摘要一致，实际浏览器页面可读。真实 DeepSeek 后台已生成两条图片 Memory，BGE-M3 向量／FTS／混合检索及上下文准备命中，从 Memory 原始材料链接回图通过。界面拖图与粘贴的区别、多图和全新桌面聊天的实际召回仍须分别验收。以下明确区分实际界面附件、实际运行内核、受控推理服务和直接脚本输入。

## 版本与输入矩阵

| 宿主／入口 | 实际执行 | 结论 |
| --- | --- | --- |
| npm Codex 0.141.0 exec，文字＋PNG／JPEG | 实际 CLI 进程、实际插件 Hook、隔离存储；阶段五另有真实 Vision 联调 | 支持固定版本文件入口；普通生成器受控 |
| 同版本 exec，同文异图、多文件、大 PNG | 实际 CLI 与临时 localhost Agent provider | 身份不能按 prompt 摘要；模型图片可能缩放 |
| 同版本 exec，空 stdin 图片-only | 实际宿主在推理前拒绝，请求数为零 | 不支持该调用方式 |
| app-server 0.141.0，localImage／data URI 图片-only | 实际 turn/start 与 thread/read，未取得 Hook 回调 | 证明协议入口，不作为该版本 Desktop 自动适配 |
| Desktop 内置 codex.exe 0.162.0-alpha.2 exec，PNG＋JPEG | 实际 CLI 进程与 Hook、耐久接收、重启后精确引用 | 已支持此固定版本 exec，保存 host_image |
| Desktop 26.1002.7124.0，用户实际聊天单 PNG | 当前实际会话的头与有界尾部、附件文件本地元数据 | 能明确取得 user/input_image、message ID、当前轮次和图片字节 |
| 同桌面内核 app-server，localImage-only 与 data URI-only 两轮 | 实际内核、明确 Hook 信任后的真实回调 | Submit 时尚无图片记录，Stop 时各有一张 |
| 同桌面内核，两轮图片-only，所有 Hook 关闭 | 实际 app-server、正式 Desktop collector、真实 Server／MiniCPM-V 35B | 两张 accepted、succeeded，保存字节与回读摘要一致；OCR 测试文字可核查 |
| 用户实际桌面会话，三次单 PNG 发送 | 已安装 App 的项目索引、接收回执、真实 35B、资产回读与浏览器证据页 | 三次均 succeeded，OCR 命中样本事实；添加附件的具体界面动作未分别记录 |
| 当前内置 0.162.0-alpha.17.2，localImage-only／data URI-only | 实际隔离 app-server、Hook 全关、正式 collector、真实 35B | 两次 accepted／succeeded，保存字节等于宿主图片；这是内核验收 |
| 当前内置 0.162.0-alpha.17.2 exec，PNG／JPEG | 实际隔离 CLI／Hook、受控 Vision 与 Memory 生成、重启及精确引用 | 完整执行测试通过；普通模型质量仍待真实验收 |
| 已保存的图片解析，真实普通模型与向量模型 | DeepSeek 后台自动生成两条 Memory；BGE-M3 真实请求、三种检索、上下文准备、浏览器材料链接 | 后端 Memory 与原始材料链路通过；全新桌面聊天尚待实际验收 |
| 实际桌面界面拖入／粘贴差异、多附件及中断组合 | 没有分项完整验收证据 | 使用桌面验收指南补测 |
| 交互 CLI TUI、未知版本、其他 Agent | 没有对应完整宿主实测 | 不开放自动捕获；通用显式上传仍可用 |

隔离运行内核测试使用临时 localhost Agent Responses provider，因此不消耗用户 Agent 账号推理。真实 Vision 使用用户授权的私有面壁配置；输入均为无敏感内容的自制图。CLI 宿主测试中的 Memory 生成器是受控组件；真实普通模型的生成与后端召回另由 DeepSeek／BGE-M3 实测证明，不能把两类证据混用。

## 实际桌面附件读取

用户于 2026 年 10 月 9 日在当前 Codex 桌面聊天发送了配置截图，并授权探测。只定位当前聊天文件，读取头和最多 2 MiB 尾部；不读取其他会话内容，也不把该截图送往摄取接口或 Vision。报告不保存图片副本、Base64、OCR、完整提示词或凭据。

| 检查 | 实际结果 |
| --- | --- |
| 安装包 | OpenAI.Codex 26.1002.7124.0 |
| 内置二进制 | 实际 --version 返回 0.162.0-alpha.2 |
| session_meta | source=vscode，originator=Codex Desktop，与内置版本一致 |
| 附件 | 常规 PNG，668×351，38,606 字节，探测期间可读 |
| 消息 | response_item／message／role=user，有独立消息 ID 和 input_image |
| 图片表示 | input_image.image_url 包含 PNG data URI；该小样本解码字节与临时文件一致 |
| 摘要 | SHA-256 前缀 846810140acb7166，两种表示一致 |
| 轮次 | 使用本次 task_started／turn_context 的 turn_id，不按“最后一张图”关联 |

一致结果只适用于该小图。阶段零 3000×2000 PNG 曾被宿主变为 2048×1365，字节摘要不同。因此 Desktop 与现代 CLI 的自动采集记录 host_image，Dashboard 提示可能经过宿主缩放，不承诺磁盘原文件相同。

## 实际 Hook 时序

在隔离 CODEX_HOME 中运行桌面同款 app-server，initialize 的 clientInfo.name 为 Codex Desktop。最初仅传 Hook trust bypass 参数没有获得信任，回调为零；不能据此判断 app-server 不支持 Hook。

随后用实际 hooks/list 获取待审阅身份和 currentHash，仅在这个一次性测试 home 写入对应 trusted_hash 并 reload 配置。两轮图片-only 产生四条实际回调：两条 UserPromptSubmit、两条 Stop。Submit 快照各没有图片记录，Stop 快照各有一张。stdin 有会话、轮次、路径、文字等字段，没有图片字节或附件列表。

当前正在进行的桌面聊天虽由用户确认信任临时探测 Hook，但未取得足够的该界面回调样本。聊天中的继续输入可能是活动轮次的 steering，也可能尚未触发配置重载；不能推断所有桌面轮次都没有 Hook。三条仅收集元数据的临时 Hook 已移除，用户其他配置与信任保留。

实现据此采用两路共用队列：CLI Hook 注册轮次并后台等记录出现；Desktop 独立采集器读取已授权项目的索引和增量记录，不依赖界面的 Hook 时序，也不连接桌面内部 IPC。

## 正式采集器与真实 Vision

正式 collector 在所有 Hook 关闭的实际 app-server 中完成两种图片-only 输入。测试使用持久工作目录授权、真实 Codex 索引、SQLite Outbox 和通用图片摄取 API，不直接调用上传函数冒充捕获。

已修复并重测的实测问题包括：索引可能位于 CODEX_SQLITE_HOME；Windows cwd 可能带 Win32 verbatim 前缀；冷启动必须沿用父进程启动时间，避免遗漏新会话的首图。索引按启用项目过滤，查询有固定 32 条上限，未知头、已删除会话和已删除项目不会阻止有效当前会话。

在新的仅 Vision 配置中，还实测了失败恢复：272 字符资产临时路径在 Windows 返回写入失败；客户端保留了两张原始暂存。修复原绑定目录的扩展路径读写后，原队列以相同 Scope 和摄取身份恢复上传。两张任务均 succeeded，保存／回读摘要一致，OCR 包含 E429、64 和 30，Dashboard 返回 200。没有更换图片或静默改投其他 Scope。

## 真实桌面发送回读与当前版本

2026 年 10 月 10 日核查用户的三次真实桌面发送。三张 PNG 的会话均存在于已安装 App 的项目索引，规范化工作目录均对应已授权的实验 worktree；来源记录为 desktop／host_image。三次任务已保存独立原图 Source 与解析 Source，真实 35B 调用返回 200，耗时约 2.7～4.0 秒。OCR 均包含 SPRUCE-27、64 MB、E429 和等待 30 秒重试；下载资产与保存摘要一致。

浏览器核验了相同 Scope 的首页、图片任务页、Memory 列表与原始材料弹窗。Dashboard 没有独立 Source 列表；图片任务路由可以直接查看未生成 Memory 的图片。已生成 Memory 的图片由记忆原始材料引用进入，保留精确历史解析。没有代操作 Codex 界面发送新附件；真实界面证据来自用户已发送的图片。

本机安装包已更新至 26.1007.2314.0，实际内核二进制返回 0.162.0-alpha.17.2。旧白名单在实际新内核的两轮图片输入中产生 image_host_unsupported、零回执。补充这一明确版本后，重新运行全新隔离 home：两轮内核 completed、两张 collector accepted，真实 35B 均 succeeded，下载等于原始宿主图片。未知版本仍关闭自动采集。已更新本地安装缓存并重启正式采集器；授权 Scope 与 epoch 保持不变。

## 真实普通模型、自动 Memory 与原始材料

用户明确提供主项目 `.env` 路径后，仅把 Generation／Embedding 配置接入实验 `desktop-demo.env`。实际普通模型为 `openai-chat:deepseek-flash`，向量模型为 `openai:BAAI/bge-m3`，1024 维 unit profile；Vision 继续使用原 MiniCPM-V 35B。主项目文件、数据库和服务不变。

只读真实预检从一份已保存解析中得到两条候选，向量请求返回有效的 1024 维数据。启用 memory family 和 30 秒检查计划后，按项目维护命令同步原数据库处理配置，未换库、换 Scope 或重置 Source。服务健康检查显示 generation、embedding 与 memory worker 就绪。

后台在显式 flush 前已自动生成两条 Memory：64 MB 缓存上限，以及 E429 时等待 30 秒后重试。后续 flush 返回 idle，消费游标与最高水位均为 20。实验 Scope 的原 20 条 Source 包括 9 份原图、9 份解析和 2 份普通内容；与开启前 SQLite 快照逐条比较，身份、正文及位置不变。五个被引用的精确解析均通过正式任务读取接口回读，Scope 与 Source 身份匹配。

真实 fts、vector、hybrid 均命中两条 Memory，`/v1/context/prepare` 返回 ready，正文包含两个结论及完整 Memory citation。实际浏览器从 Memory 展开“原始材料”并点击材料链接，显示对应历史原图、OCR、宿主表示和 MiniCPM-V 来源。未手写 Memory、修改候选规则或使用受控生成器替代这个结果。

这些结果证明真实后端生成、检索及来源回图；全新 Codex 桌面聊天是否实际调用、使用或自动注入该记忆，仍需按桌面指南独立验收。单个自制样本的成功不代表阶段七的工作图片质量评估完成。

## 隔离与材料位置

| 本地忽略目录／材料 | 内容 |
| --- | --- |
| phase0/codex/ | 原始 0.141.0 探测、合成输入和受控 provider 事件 |
| desktop-attachment-probe/ | 当前真实附件的脱敏元数据；没有截图副本 |
| desktop-integration/app-server/ | 0.162.0-alpha.2 真实 Hook 信任、stdin 和时序 |
| desktop-integration/app-server-watch-final/ | 正式无 Hook collector 的实际运行内核输入、索引和 outbox |
| desktop-integration/real-collector-summary.json | 真实 collector＋35B 首次两张成功的摘要 |
| desktop-integration/capture-only-readback.json | 仅 Vision 运行配置的恢复上传、OCR、字节及 Dashboard 核查 |
| desktop-integration/desktop-setup.json | 本机桌面验收工作目录、独立 Scope、插件目录和地址 |
| desktop-integration/2026-10-10-desktop-readback.json | 三张真实用户桌面图的 Source、模型、OCR 事实和资产摘要核验 |
| desktop-integration/2026-10-10-dashboard-proof.png | 浏览器中的原图与解析证据页截图 |
| desktop-integration/runtime-alpha17-baseline/ | 当前内核拒绝采集的实际复现，只有合成图片 |
| desktop-integration/runtime-alpha17-verified/readback.json | 兼容修复后实际新内核、正式采集与真实 35B 的两轮结果 |
| desktop-integration/memory-enable/ | 普通模型预检、配置与 SQLite 开启前快照、真实处理／检索回读及 Memory 原始材料截图；凭据只在本地 |

上述相对路径位于 `.powercontext/image-source-experiment/`，均被 Git 忽略。公开文档不保存账号、完整用户聊天／消息 ID、真实附件或认证材料。

实际 CLI 验收程序为 `tests/e2e/test_image_codex_live.py`。显式设置 POWERCONTEXT_TEST_CODEX_BIN 后运行；可选 POWERCONTEXT_TEST_VISION_ENV_FILE 才使用私有真实 Vision。程序隔离 CODEX_HOME、SQLite、资产和 Agent provider。测试中的临时 Hook 信任设置只作用于该隔离进程，不绕过用户全局审阅。

完整测试、审查与修复记录见 [实施过程](implementation-log.md)，实际界面验收方法见 [桌面测试](desktop-testing.md)。仍未完成：真实界面组合、全新桌面聊天的实际召回与自动注入、1B、大样本质量评估和完整资产配对的备份恢复。
