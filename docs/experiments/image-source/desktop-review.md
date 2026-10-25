---
title: Codex 图片采集扩展代码审查结论
description: Open Code Review 调用、Delegate 审查覆盖、已修复问题和当前验收限制。
---

# Codex 图片采集扩展代码审查结论

本轮按用户要求调用 Open Code Review，并完成本地主持审查与修复。没有遗留已确认的阻塞性代码问题。实际桌面界面和普通模型自动 Memory／新聊天召回仍是验收待办，不因代码审查完成而标记通过。

## 审查方式和范围

基准为 `6b2f6e8a4c1aac59fca75662aca26d78748bfa29`，审查对象为实验 worktree 的本轮工作区变化。先保存开始时文件摘要，排除既有阶段代码和用户已有修复，按 `AGENTS.md`、`REVIEW.md`、固定图片契约及多端扩展要求审查。没有暂存、提交或发布审查评论。

首次实际执行 `ocr review`，外部模型为 deepseek-flash，100,000 token 预算。OCR 规划阶段估计首组需要 294,736 token，触发预算限制，17 个选择项全部失败，完成文件为 0；其“0 finding”不代表审查通过。

随后使用同插件的 [Delegate Skill](https://github.com/alibaba/open-code-review/blob/main/skills/open-code-review-delegate/SKILL.md)：实际执行 `ocr delegate preview` 和 `ocr delegate rule` 获取选择与规则，由当前 Codex 按文件、调用方、持久化和恢复边界进行审查。此模式不由 OCR 再调用外部 LLM。初始选择 17 个，17 个均完成，跳过 0，覆盖率 100%；另审查了后续的 Windows 资产路径修复、配置引导、相关测试、依赖解析路径和文档。

| 完成的选择项 | 核查重点 |
| --- | --- |
| hooks/hooks.json | Submit／Stop／Interrupt、固定命令、预算与用户信任 |
| settings.py | 持久授权优先级、地址绑定、可选配置不损坏普通召回 |
| runtime/composition.py | MiniCPM 单 system 消息适配，保留提示与输出 schema |
| HTTP generated/models.py、schema.py | 与 OpenAPI 生成一致，没有手改生成代码 |
| Dashboard labels.json、labels.en.json、presenters.py、image-evidence.html | host_image 提示、精确历史来源、文本转义和 Scope 资产链接 |
| openapi/powercontext.yaml | 三个 optional 来源字段、原操作不变 |
| image_attachments.py | 版本门控、消息与轮次身份、字节预算、部分记录与原文件校验 |
| image_capture.py | 默认关闭、Scope 无回退、短 Hook、旧完整回执兼容 |
| image_capture_worker.py | 两端共用锁、断网恢复、重新授权与旧游标退出 |
| image_desktop_watch.py | 项目过滤、只读索引、冷启动首图、异常会话隔离和查询上限 |
| image_outbox.py | 原子暂存与偏移、字节冲突、清理边界、连接释放 |
| image_capture_config.py | 明确项目授权、隐藏进程、开关、状态和恢复入口 |
| images/models.py | 旧权威 Source 指纹保持，现代来源往返一致 |

## 已修复问题

| 问题及可观察后果 | 修复与证据 |
| --- | --- |
| 宿主 Submit 时没有图片记录，立即上传会遗漏附件 | 注册轮次后增量等待；实际 Hook 时序和无 Hook Desktop 采集均验证 |
| Desktop 子进程冷启动晚于新会话创建，首图可能被当作历史 | 使用启动者时间与授权时期；实际内核两轮通过 |
| Windows 索引路径带 verbatim 前缀或索引放在独立 home，无法匹配项目 | 统一路径键，使用 CODEX_SQLITE_HOME，实际主机验证 |
| 已删除会话／项目或未知头阻断其他当前会话 | 按文件隔离，退休已删除观察记录；回归测试继续接收有效图片 |
| 关闭再开启时，旧活动轮次游标会读取关闭期间的消息 | 授权时期固定；旧游标退出，已暂存投递独立保留；回归仅收到关闭前和新轮次图片 |
| 保存的 disable 被继承的 opt-in 覆盖 | 捕获和投递边界均优先检查持久关闭；回归验证 |
| 可选图片配置损坏／地址变化会让普通设置无法加载 | 普通 settings 跳过该可选来源，实际图片边界仍拒绝；恢复命令可用 |
| 同一附件身份换字节，在上传后 bytes 清除的状态下被当作重复 | 保留 input_digest 并比较，冲突不覆盖；回归验证 |
| SQLite connection 的 with 只提交事务，没有关闭连接 | 明确关闭连接；Outbox 事务与只读索引均释放资源 |
| 新增 nullable 来源字段会改变历史 Original Source 指纹 | 只省略三个新添的空字段，保留旧字段；旧指纹和新来源往返测试通过 |
| MiniCPM 普通 Chat 模板拒绝多条 system 指令 | 在目标 provider 合并系统指令，保留 schema；实际请求 200，Memory 质量仍待测 |
| Windows 深层资产临时文件达到 272 字符，保存返回 500 | 保留数据库绑定根目录，资产目录验证与 I/O 使用 Win32 扩展路径；真实原队列恢复，长路径接收／重放／重启回读回归通过 |

用户原先在 tasks.py、vision.py、worker.py 和 server/images.py 的修复摘要保持不变。service.py 仅新增 Windows 扩展路径处理，未撤销其已有耐久写入、维护和恢复逻辑。

## 验证及限制

主要回归为 413 passed、6 skipped；后续图片、恢复、Dashboard、配置和采集修复检查为 82 passed，最后针对更新的采集器与 Windows 长路径为 24 passed。数字分别对应不同检查集，存在重叠，不合并成一个测试总数。两版真实 CLI 分别通过 1 项验收。Ruff、受影响文件 ty、API／JS 生成一致性检查通过。

正式 Desktop collector 在实际 0.162.0-alpha.2 内核、所有 Hook 关闭时捕获 localImage-only 和 data URI-only；真实 MiniCPM-V 35B 保存解读，原图摘要、OCR 与 Dashboard 核查通过。失败恢复也使用同一暂存材料完成，没有重造来源。Agent Responses provider 和 CLI Memory 生成器受控；普通生成模型、新桌面聊天召回、真实界面拖图／粘贴与 TUI 尚未完成。

当前本地索引查询使用 existing archived/cwd 索引，IN 查询每项目最多 32 行；实测 10 次平均约 0.49 ms。此数据只描述当前机器与数据集，不代表其他规模的性能承诺。工作图片质量、大样本成本、长期使用和备份恢复继续留给阶段七。

原始 OCR 失败报告、delegate 选择／规则、逐文件覆盖清单、本地结果和实测摘要保存在被忽略的 `.powercontext/image-source-experiment/desktop-integration/`。验收操作见 [桌面测试](desktop-testing.md)。
