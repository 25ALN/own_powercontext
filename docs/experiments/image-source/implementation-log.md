---
title: 图片 Source 实验功能实施过程记录
description: 按阶段记录目标、实际变更、验证证据、限制和后续工作。
---

# 图片 Source 实验功能实施过程记录

## 2026-10-25：PR #1956 新 head CI 修复

目标：逐项核对 74dada2685f979a6fa52a063c93bfb844a5cb19c 的 Main、Desktop、E2E harness 和 Skill guidance 失败日志，在相应环境复现并修复真实问题，保留测试语义和支持边界，完成检查后更新同一 PR。沿用隔离 worktree、临时数据库和测试端口，不操作既有服务或私有模型配置。

### 失败归因与修复

74dada26 的 14 个失败 job 归并为四类，均由本轮 RFC 架构提交引起：

| 失败集合 | 直接原因 | 修复 |
| --- | --- | --- |
| Main 的四个 Python 版本 unit/e2e，以及 OceanBase acceptance | 新增 image-analysis 及图片 Source 读取能力后，健康检查、Access、SDK 的精确能力列表仍为旧值 | 更新完整列表的精确断言，保留权限、降级、写入和回读语义；没有改成宽松包含检查 |
| 三个平台的 Desktop | OpenAPI/Rust wire 已更新，独立 IPC 导出未重新生成 | 用 export_ipc 官方程序重新生成并执行原 --check |
| Dify tools | 选中的 OpenAPI 组件与七个生成产物失配 | 通过 generate_contract.py 重新生成声明和 contract.json |
| Skill guidance | OpenAPI 参数枚举扩展后，共享中立工具参数参考仍为旧值 | 用 validate_suite.py --write-tool-contract 更新参考，不改 Skill pin、工具目录或五个用例的控制规则 |

本地进一步发现 Windows 的 Dify 生成器诊断／测试快照键使用平台分隔符，而引用使用 POSIX 相对路径。6b976859 主干已有相同 str(relative_path) 实现，故这是原有的工具兼容 bug，已统一为 as_posix 并保留原漂移拒绝断言，不需另开 issue 才能修复。OceanBase 此轮没有表现出存储故障；前置断言失败导致未生成 replay evidence，空证据扫描是后续失败，不是另一个已证实的后端 bug。

### 本地验证

- 修改前用实际 HTTP / SQLite 复现相同的 11 个 unit 失败；修改后运行 tests/test_server.py、tests/test_access_http.py、tests/e2e/test_runtime_server.py、tests/e2e/test_candidate_review.py：100 passed、4 个未配置后端的环境跳过。
- 官方 cargo export_ipc --check 先复现漂移，重新导出后通过；Desktop typecheck 和 98 项 UI 测试通过。没有更改 OpenAPI 或 Rust wire，既有协议资格摘要仍适用。
- Dify generate_requirements / generate_contract 检查、独立 Python 3.12 SDK 的 Ruff / ty 通过。固定 Dify 1.17.1 宿主 8387590a、daemon 1310a18b 与实际 Go 探针后，完整 SDK 集合 82 passed；真实 Server / SQLite / SDK 子进程 HTTP 链路 3 passed。宿主兼容探针执行固定源码的帮助函数与 daemon 序列化，不等同于完整 Dify 服务部署。
- Skill pin、validate_suite 通过，28 项 Python 回归通过；使用官方 checksum 校验的 Windows skill-up 0.12.0，原生 validate 和 run.py --dry-run 的五个用例均通过，没有执行付费模型。
- 全部非类型 pre-commit hooks、Linux 目标的全量 ty 和 diff 检查通过；Windows 原有类型诊断仍单独记录，未宣称本机全部类型检查通过。Dify SDK 的 gevent/Pydantic 警告未导致测试失败；没有把未独立复现的警告当作业务缺陷。
- 未改动业务 Runtime、权限实现、迁移格式、模型预算或 CI 的测试选择。GitHub 新 head 必须重新执行完整 Python 矩阵、Dify/Skill、Desktop 与实际 OceanBase 验收后判断结果；旧 head 的通过项不冒充新 head CI 通过。


## 2026-10-25：RFC #1972 架构对齐实施

目标阶段：依据 RFC #1972 `919d6b30b8c42bc83d38477b1379e08f09155b81` 完成共享媒体三表、image-analysis Artifact 与确定性解释发布、Supervisor 接入、Memory 逐陈述排序依据、五个公共操作、版本化迁移及兼容回读。用户明确授权实现、补齐迁移和测试，并更新 PR #1956。起点为 `9abb3628`，沿用独立 worktree 和临时数据；不操作现有服务或私有模型。

### 实现与兼容

- 原图改为 image.original Source；解释由 image-analysis Artifact 拥有，按精确版本发布确定性 image.interpretation Source。删除原七表／文件资产和独立 worker 实现，保留 experimental Definition 的历史解码。
- 仅新增 pc_media_assets、pc_media_processing_requests、pc_media_capacity，共享 BLOB、固定输入与首次回执、类型化进度、claim 和派生计数。接收与结果发布分别为完整 SQLite 事务；活跃 kind/Scope 覆盖索引避免调度扫描终态历史。
- 处理复用 ArtifactProcessingSupervisor、fencing 和独立 family 预算；Vision-only 子进程可工作。重试固定旧 profile；reparse 固定当前 profile，校验精确结果版本、配置摘要和被替换请求 state_version。未知尝试占预算，晚到失效 claim 不发布。
- Memory 消费位置与原始事实锚点分开，保存精确 kind/text 绑定的逐陈述证据、日期及适用条件。抽取和协调均选择有界支持 ID；旧/未知冲突保留当前身份，独立事实可以前进。已有记录仅对授权、单一精确文本来源恢复依据；缺失保持未知，损坏明确失败。
- media bundle 使用共享维护锁、只读 plan、精确计划接受、SQLite BackupProvider 和冻结 DDL。旧文字记录不改身份、payload 或 journal；七表／文件导入明确拒绝，尚不提供转换器。原生完整 SQLite 备份包含媒体；逻辑归档 v1 对媒体提前拒绝，普通 Memory 支持排序元数据复制。
- 五个原生操作替换七个实验操作，通用 Source/Artifact/capabilities 承担读取。CLI、Dashboard 和 Codex Outbox 统一新回执；图片解码留在可选 images extra，双语配置及迁移文档同步。
- 主干从 558b8262 更新到 6b976859。保留原分支合并拓扑并合入新主干，不丢弃此前冲突解决；新主干跳过无关候选协调时，初次 Memory 也保留陈述支持和精确图片 Artifact lineage。

### 执行验证

- Memory、归档、权限、图片 Evidence/Dashboard、存储及 HTTP 扩大集合：362 passed、66 skipped。同步主干和补充首次陈述支持后，Atomic Memory 与迟到证据／独立事实集合：108 passed、3 skipped；集合重叠，不累加。
- 固定提交 checkout 中 `tests/e2e/test_media_processing.py`：5 passed，159.89 秒。实际 Supervisor spawn、HTTP、OS 终止、未知尝试恢复、429 预算、显式失败 retry、旧响应失效均执行。一次运行被本轮同步文件的中间状态干扰而导入旧模块，已在固定 checkout 完整重跑，不把该运行算作行为缺陷或通过证据。
- API/JS/Desktop/图片生成契约：70 passed。授权、通用 records、HTTP 准入和 integration manifest：103 passed、4 skipped。最终活跃索引／冻结 schema 后的存储及迁移集合：11 passed。
- Codex、图片 CLI、向导及 archive CLI：393 passed、6 skipped；两项严格 deadline 用例分开执行，固定 checkout 为 2 passed。并行负载下曾出现 1 秒阈值和 Stop 外层超时波动；没有更改原断言或生产逻辑。单独计时观察实际 body 超时约 0.302 秒。
- wheel `powercontext-1.4.1.dev194+g4894785a6-py3-none-any.whl` 在六个独立环境验证：Python 3.11/3.14 的 builtin、images、server+cli。均执行 `python -I scripts/ci_role_smoke.py` 并通过；两个全新 Server 环境额外确认没有 Pillow，仍可导入和运行。
- 该 wheel 的 578 个运行时文件与 4894785a6efd65d1f0ce89e4f2af3250ab2a7ae7 逐个核对一致（统一换行符后比较），原生 Desktop 客户端对四种真实 SQLite Server 模式全部通过。新产品资格 sqlite-atomic-4894785a-v1 绑定实际源码、合同和 wheel 摘要；原 bundled qualification 回归通过。详见 desktop/VALIDATION.md；后续活跃请求索引不改变该 HTTP 合同。
- Desktop lint、98 项 UI 测试和 production build 通过；DSH 官方 build 与 427 passed、1 skipped 通过。网站 lint、测试及生产构建完成，验证 980 个公开页面和内部链接。Node 为 22.23.2，保留 Desktop 声明 24.14.1 的 engine 提示。
- Python/JS 官方生成检查、Desktop 官方生成、根与 e2e/bub uv 0.10.12 lock 检查、integration manifest 文档检查、Linux 目标 ty 和非类型 prek 通过。Windows 原生 ty 仍为 34 项既有诊断，不声称全平台类型检查通过。

### 容量测量和复审

`evaluation/media_capacity.py` 在最终 schema 上比较容量计数与事务内 SUM/COUNT，包含 1 GiB/131080 个资产、896 起步至 1024 活跃请求、1/8 路并发和队列上限。八路聚合比较出现 13 次 SQLite 锁超时；完整报告同时记录启动核对、SQL 计划、锁时间、新的约 7 MB PNG BLOB 写入及并行文档构建限制。数字以 media-capacity-results.json 与 integration-review.md 为准，不声明通用性能保证。

使用 Open Code Review delegate preview/rule 对本轮实现差异逐文件复审，并按 REVIEW.md 检查端到端写入、授权、恢复、历史读取和消费。生成契约通过官方检查，新增活跃索引问题已修复并复测；审查清单与规则保存在忽略目录 .powercontext/ocr-1972-*。这是本轮架构变更范围的审查，不冒充重新审阅所有未变动的早期采集实现。

外部视觉模型质量、真实 Codex Desktop 拖图／粘贴、全新聊天自动召回和原生 macOS/Linux 采集器没有重新验收。推送同一 PR 后以新 head CI 为准；不执行合并。


## 2026-10-25：PR #1956 rebase 与既有 review 核对

本轮目标属于主干集成维护：将同一 PR rebase 到 `upstream/master 558b8262d0f332a1eef8759faf3423fce5357155`，解决冲突并核对 GitHub 已有 review。起点为 `85d0898fa6822e1c03b7fc8a2743481202667e9a`，备份分支为 `codex/backup-pr1956-85d0898f`。使用独立 worktree `pr1956-rebase-1025`、独立虚拟环境和临时测试数据库、资产，不操作现有服务或私有模型配置。

- 执行 `git rebase --rebase-merges upstream/master`，保留合并拓扑和 16 个功能/修复提交的原始身份，重建主干集成 merge 和 Desktop 资格记录提交。共处理 13 个冲突文件，保留双语 Codex 图片与输入隐私说明、Memory 完整正文及身份校验、Server 请求标识与图片错误边界，以及两侧的权限和协议回归。
- Python、JS 和 Desktop 契约由官方脚本重新生成；最终 OpenAPI 与原 PR 完全相同，摘要仍为 `f86ca993b9e39e75948a83a4a038c0eaa2aadf6e0d14c60b37fcc9be83e2dfcb`。保留历史 Desktop 资格记录；已有资格不代表对当前二进制或真实图片宿主的新增验收。两份 uv 锁文件使用与 CI 一致的 uv 0.10.12 生成，保留主干依赖更新。
- 三条代码 review 均已有行为修复：容量压力保持 watching 和原游标，兼容旧 image_outbox_full 错误；投递前按授权筛选再分页，发送前重查授权；builtin 直接声明 platformdirs，CI 同时覆盖 builtin/images。没有代审查者关闭线程。
- `python -m pytest -q tests/codex_plugin/test_image_delivery.py tests/codex_plugin/test_image_desktop_watch.py`：37 passed，包含容量自动恢复、显式重试、旧错误游标、跨项目投递和重新授权隐私边界。
- `uv build --wheel --out-dir .powercontext/role-wheel` 后，在四个新环境分别安装该 wheel 的 builtin/images extra，使用 Python 3.11、3.14 执行 `python -I scripts/ci_role_smoke.py <extra>`：四组全部通过，包括运行时打开、Memory 写入、关闭、重开及搜索。未借用开发依赖。
- 使用 `python -m pytest -n 2 -q --basetemp .powercontext/t-integrate` 检查图片 E2E、采集/生命周期、Access、Memory 协调及重试、HTTP 资源限制、MiniCPM、ZCode 协议和 API/JS/Desktop/图片契约：257 passed、1 failed。失败位于孤立资产恢复用例的重命名，目标路径超过 Windows 常规路径限制；以 `--basetemp D:/t/pc1956-20261025-orphan` 单独重跑该项，1 passed。没有改动实现、预算或断言。权限并发回归另行执行，2 passed；与集成集合重叠。
- `pnpm --dir desktop lint`、`test`、`build` 通过，98 项 UI 测试通过。工具为 Node 22.23.2、pnpm 11.13.1、Java 21；项目声明的 Node 24.14.1 engine 提示保留，不声称使用了该固定版本。
- Python/JS 生成 `--check`、Desktop 官方生成检查、两个 `uv lock --locked`、integration guidance/capability 文档检查及 `ty check --python-platform linux` 通过。完整 `prek run -a` 中 JSON 格式 hook 规范化换行后复跑通过；原生 Windows ty 报告 34 项诊断，涉及 PR 未改动的 POSIX 原生索引、CLI 和测试代码。其余 hooks 通过；不把 Linux 目标结果当作原生 Windows 全库类型通过。

架构反馈尚未全部完成：RFC #1972 已提交且有批准记录，但尚未合入；RFC 使用 image-analysis Artifact 和三张共享媒体表，当前实现仍使用解析 Source 和七张实验表。整合审查报告明确记录这些差异。此次使用 Open Code Review delegate 筛选/规则检查与 review 对应的五个文件，并检查冲突和相关调用路径；其余 70 个默认可审查文件不属于此次既有反馈核对范围，不宣称完成新一轮全 PR 审查。

本轮没有新增真实 Codex Desktop 上传、模型调用、新会话召回、配对恢复或原生 macOS/Linux 生命周期验收。下一步是按 RFC 确定实现与迁移方案，并完成这些真实验收。以原 head 的精确 lease 更新同一 PR；不执行合并，不沿用旧 head 的 GitHub CI 结果。

## 2026-10-23：CI 修复与主干冲突处理

本轮属于集成维护：检查 `40e5b7e9` 的 GitHub Actions 失败，合入当前主干 `c535d816`，处理契约生成文件、依赖锁及双语文档冲突，完成相关验证后更新同一 PR。沿用隔离 worktree 和测试数据；不操作真实宿主或私有模型服务。

主干集成保留图片开关、原图与解析证据、容量和透明缩略图修复，同时接入主干的自动输入隐私、CLI builtin 依赖和 Memory 协调决策校验。六个冲突文件已处理；Python、JS、Desktop 契约以及 DSH bundle 由官方脚本重建，双语 Codex 文档保留图片材料与自动输入隐私两节，两个 uv 锁文件按合并后的依赖生成。

API／JS／Desktop／图片契约、图片投递与 Dashboard、Scope binding、system CLI 定向集合首轮为 369 passed、2 skipped、2 failed；两项失败发生在 Desktop 契约尚未生成时，生成后 `tests/test_desktop_contract.py` 为 2 passed。两个锁检查、Python／JS 生成漂移检查和 Linux 目标 ty 检查通过。Desktop UI 测试 32 passed。

GitHub Desktop 三个平台均失败于 `bundled_qualification_allows_explicit_scope_browsing`：当前 OpenAPI 摘要没有对应的内置 Server 资格记录。在本机 Windows/Rust 1.95.0 定向执行原测试复现相同失败。使用合并提交 `ec32d953be18b52ace1099ceb7107f6a89ba66c9` 构建独立 wheel，执行原生客户端与真实 SQLite Server 协议验收后添加 `sqlite-atomic-ec32d953-v1`；保留所有历史记录与原测试断言。精确来源、契约、wheel 和源码清单摘要及复现命令记录于 `desktop/VALIDATION.md`。

`uv build --wheel --out-dir desktop/.artifacts/server-wheel`、`fixture_qualification.py --server-commit ec32d953be18b52ace1099ceb7107f6a89ba66c9`、`cargo build --locked --manifest-path desktop/src-tauri/Cargo.toml --example server_probe` 和 `uv run --no-sync python desktop/tests/real_server.py` 完成。Windows 11 AMD64 / Python 3.14.5 / Rust 1.95.0 下，匿名、Bearer、HTTPS 显式 CA/base path、注入 provider 强制授权四种真实 Server 模式均通过。556 个 wheel 运行时文件经 CRLF/LF 规范化后与记录提交一致；协议验收不代表图片原生 UI、安装包、macOS 或发布验收。

补充验证：图片证据／解析／恢复／标准集成、Memory 协调和 MiniCPM 集合 69 passed；资格 fixture 测试 5 passed；`cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml` 全部 53 个测试通过，包含最初失败的 bundled qualification 用例。Desktop lint、32 项 UI 测试和 build、`cargo fmt --check`、`cargo clippy --all-targets -- -D warnings` 通过，非类型 pre-commit hooks 通过。

本机默认 Windows `ty check` 仍因已有 POSIX 接口及 `url2pathname` 弃用提示报告 34 项诊断；Linux 目标检查通过。没有修改检查配置或放宽类型规则。

原 E2E harness 的 OceanBase 失败已读取上传产物确认：21 项数据库回归通过，正常批次与 LoCoMo 场景通过，fail-fast 场景启动时 Docker Hub 的 Python 基础镜像 HEAD 请求返回 HTTP 500，尚未执行任务。没有因此更改产品代码、失败策略或验收断言；新提交将重新触发该远端场景，本机没有声称执行 OceanBase Docker 验收。

## 2026-10-23：PR #1956 代码审查与缺陷修复

本轮目标属于集成维护：使用 Open Code Review 审查 `0bc42d0b..bada5053`，核实并修复可复现问题，验证后推送同一 PR。使用独立 worktree `pr1956-review-1023` 和隔离测试数据库、资产及宿主配置；不操作现有桌面服务或私有模型。重点检查队列容量回收、授权、任务恢复和历史证据读取。真实宿主及模型验收边界保持不变。

Open Code Review 的外部模型执行出现请求和代码搜索超时，仅产生 3 个文件的完成标记，未生成完整报告，已停止该次执行。随后按同插件的 delegate 流程读取文件清单、规则和差异，完成全部 74 个可审查文件的检查（跳过 0，覆盖率 100%）。默认过滤的 58 个文件不计入此覆盖率；另外读取实验契约、实施记录及相关测试，并执行下述验证。

确认并修复两项中等严重性问题：

- Outbox 达到任务上限且包含活动读取器或未投递附件时，原清理逻辑按“可删除任务数”应用总容量偏移，导致已有完成任务仍无法腾出位置。现在按全部任务计算需要释放的数量，只删除最旧且可安全回收的任务；活动读取器、未投递附件、永久投递错误附件和容量暂停任务均保留。四种保护场景的回归在修复前全部失败，修复后通过。
- Dashboard 缩略图仅识别 RGBA 透明度，LA、调色板透明及 RGB 透明色 PNG 会被错误合成为黑底。现在先规范化为 RGBA，再缩放并合成白底；通过 HTTP 读取缩略图和原图验证四种透明表示，并确认原始字节不变。修复前三种非 RGBA 场景失败，修复后全部通过。

验证结果（Windows / Python 3.14，均使用隔离临时目录）：

- `uv sync --locked` 通过；`uv run --no-sync pytest -q tests/codex_plugin/test_image_delivery.py tests/codex_plugin/test_image_desktop_watch.py`：37 passed。
- `uv run --no-sync pytest -q tests/e2e/test_image_dashboard.py`：9 passed。
- 扩展执行 Codex 插件、图片 E2E、契约、CLI、Vision、配置向导与诊断用例：455 passed、6 skipped、3 failed。失败均为宿主超时断言，分别是 `test_native_helper_does_not_forward_unusable_credentials[invalid_header]`、`test_scope_response_body_respects_the_per_request_timeout`、`test_stop_process_exits_within_the_host_deadline_against_a_slow_server`。它们随后单独执行均通过；未修改相关实现、阈值或断言，也不将该次扩展执行计作全绿。
- Ruff 检查和格式检查通过。`uv run --no-sync prek run -a` 的非类型检查通过；默认 Windows `ty check` 因已有 POSIX 专用接口（包括 `fcntl`、`resource`、`os.O_NOFOLLOW`、`os.mkfifo`）报告 32 项诊断。`uv run --no-sync ty check --python-platform linux` 通过。

这些结果覆盖本地 HTTP、SQLite、受控回环模型服务及模拟宿主事件；没有新增真实 Codex Desktop、Linux/macOS 服务管理或私有视觉模型验收结论。

## 2026-10-20：PR #1956 主干集成

本轮目标是将现有 PR rebase 到 `fe50f8940`，解决冲突、验证图片与主干接入行为，并以精确 lease 更新同一 Draft PR。原实验 worktree 有未提交文档，保留在原处；本轮使用独立 worktree `D:/code/git-project/powermem/pr-1956-rebase` 和分支 `codex/rebase-pr-1956-20261020`，远端起点为 `21df99c841b154975e838fff6618a91c8c191350`。不操作已有服务、私有数据库或模型配置。

已完成 11 个提交的重放。保留主干启动上下文及回执、Working Note 资格限制、Atomic Artifact 搜索、Dream 契约和 Dashboard 审核/历史/Scope 读取；将图片采集、可读解析证据及原图链接接入对应流程。Python/JS/Desktop 契约从合并后的 OpenAPI 生成，DSH 使用官方构建重建。保留主干较新的 ZCode Skill pin 和已合入的 Memory 延迟审计实现；锁文件补齐 images extra 继承的 `jieba`。

首轮图片 Dashboard 与契约检查通过；Codex 启动上下文测试遇到 Windows 临时路径长度限制，后续使用隔离 worktree 内较短的 pytest 临时目录运行。短路径下定位到主干回执写入在 `os.replace` 时仍持有临时文件句柄；直接执行主干文件复现 WinError 32。Codex 和 Claude Code 的相同实现改为先完成 fsync 并关闭句柄，再原子替换，锁、回执内容及权限保持生效。故障输出测试仅容忍关闭故意破坏的 Windows pipe 时再次 flush 产生的清理错误，保留回执删除和重试断言。增加同时开启图片采集与启动上下文的回归，验证上下文注入和首次查询回执继续有效。

Memory 并发审计回归接入主干实际使用的 `_decision_snapshot`，仍在读快照后插入真实独立提交，并检查创建/替换、回读、审计及过期 ETag 拒绝。原注入点已不再进入原生授权流程，未删除 `interleaved` 断言。

本机 Windows / Python 3.14 验证结果（集合有重叠，不相加）：

- 图片、Codex、API 契约、Access 和 Dashboard 联合集合执行 `uv run --no-sync python -m pytest -n 2 -q --basetemp .powercontext/t1`，首轮 539 通过、12 跳过、11 失败。失败包括上述回执问题、两条失效的审计注入，以及一条并行负载下的超时上界断言。
- Working Note、Topic Memory、启动上下文、Dream 审核、Dashboard、向导、guidance 与 ZCode 联合集合使用同一命令及独立 `t2` 目录，311 通过、2 失败；两条失败为实际宿主 Hook 子进程的回执写入。
- 修复后集中复核 Codex/Claude Code 启动上下文、实际子进程 Hook 链路、并发审计与超时上界，使用两 worker 和独立 `t8` 目录，52 项全部通过。超时用例的预算与断言未修改。
- Desktop `pnpm --dir desktop lint`、`test`、`build` 通过，32 项 UI 测试通过；DSH 官方 build、427 项单元测试（1 跳过）及 9 项 E2E 通过。
- API/JS/Desktop 与 Dify 契约生成检查、集成能力文档、ZCode pin、根和 harness 锁文件检查通过。Ruff、格式检查、Linux 目标 `ty check` 通过。完整 `prek run -a` 的非类型检查通过；Windows 全库 ty 仍报告未修改的 POSIX 原生索引相关诊断，不宣称全平台类型检查通过。

上述模型均为受控 provider；实际子进程 Hook 链路不等于真实 Codex/Claude Code 客户端或私有模型验收。保持 Draft，真实 Atomic Memory/新桌面召回、配对恢复及最终完整复审继续按原验收清单推进。本次推送只使用远端原 head 的精确 lease，新 head 的 GitHub CI 独立核查。

本记录用于继续开发和检查实验进度。每次 AI Coding 在开始时登记目标，在完成或中断前补齐实际结果。功能范围与阶段验收以 [建设指南](README.md) 为准；这里保留执行历史，不替代当前数据和操作契约。

## 当前工作状态

| 项目 | 状态 |
| --- | --- |
| Worktree | D:/code/git-project/powermem/powermem-image-source-experiment |
| 分支 | codex/experiment-image-source |
| 起始提交 | 6b2f6e8a4c1aac59fca75662aca26d78748bfa29 |
| 建设准备 | worktree 已创建，指导文档和 AI 编码约束已保存 |
| 当前阶段 | 阶段二至六已实现固定支持范围；继续 Desktop 分项与新会话召回验收，再推进阶段七 |
| 运行时代码 | 原图与解析 Source、可恢复 Vision、CLI／Desktop 共用采集、配置引导、Memory 文字证据和 Dashboard 回图已实现 |
| 模型配置 | 真实 MiniCPM-V 35B 识图、DeepSeek 自动 Memory 与 BGE-M3 后端检索已验证；1B 未配置 |
| 宿主支持 | CLI 0.141.0／0.162.0-alpha.2／alpha.17.2 exec；Desktop 固定内核与用户三张 PNG 已验证，界面组合与新聊天自动召回待补；TUI 未开放 |
| 上游状态 | 未推送，未创建 PR，未发布 |

## 阶段进度

| 阶段 | 目标 | 状态 | 验收依据 |
| --- | --- | --- | --- |
| 零 | 真实输入与模型接口验证 | 固定范围完成；1B 与未知宿主不计为通过 | 公开资料、实际 CLI／Desktop 内核及用户 PNG、真实 35B；详细矩阵见宿主证据 |
| 一 | 数据与操作契约 | 完成 | 唯一 OpenAPI 中的 schema 与未激活声明、生成检查、73 项契约与 MCP 测试通过 |
| 二 | 原图摄取与回读 | 完成 | 24 项图片验收、原图／Source／契约组合 46 passed；Open Code Review 模型审查及委托补审完成，有效问题已修复 |
| 三 | 视觉解析与恢复 | 完成 | 真实 35B、严格输出、有限重试与持久恢复；阶段三四专项记录及正式 collector 回读 |
| 四 | 文字证据与跨会话复用 | 本地链路完成；新桌面聊天待独立验收 | 既有跨会话受控测试；真实 DeepSeek／BGE-M3 后端检索及精确图片材料回读 |
| 五 | 经验证的 Codex 自动捕获 | 限定版本与基本输入完成；界面组合待补 | 实际 exec／Hook、无 Hook Desktop 内核、用户三张 PNG 与真实 Vision |
| 六 | 配置引导与最小证据查看 | 完成 | 配置向导、真实浏览器任务页与 Memory 原始材料回图，既有测试和审查修复 |
| 七 | 实验价值与维护成本评估 | 待开始 | 无实测数据 |

## 记录规则

每次工作使用独立条目，日期按 Asia/Shanghai 记录。条目内容至少包括目标、阶段、前置条件、改动文件和实际行为、执行命令与结果、证据级别、未完成事项、范围变化和下一步。完成阶段后更新上表，但保留已有条目。

每个验证结果标明证据类型：文档检查、离线行为测试、直接 hook 调用、真实 HTTP 与数据库、真实模型、真实宿主。模拟成功不能升级为真实外部服务或宿主验收通过。

凭据、未脱敏图片与完整请求不写入记录；私有材料保存在 .powercontext/image-source-experiment/。长篇原始输出也放入忽略目录，记录可定位的摘要和结果。

## 2026 年 10 月 7 日 建设准备

### 目标与前置条件

在当前项目同级目录建立独立 Git worktree，保存实验建设指南和实施记录要求。以主 checkout 当前提交为基线；图片运行时代码和私有模型执行不属于本次准备工作。

### 实际变更

- 从提交 6b2f6e8a4c1aac59fca75662aca26d78748bfa29 创建本地分支 codex/experiment-image-source 和同级 worktree。
- 创建 README.md，固定原图 Source、后台视觉解析、解析 Source、文字证据与既有记忆流程的链路。
- 创建 implementation-log.md，登记阶段状态和持续记录方法。
- 在实验 worktree 根 AGENTS.md 增加指南入口和每次 AI Coding 更新实施记录的要求。
- 未改动原主 checkout 的文件；未复制其未跟踪 docs/my/ 或本地环境。

### 验证结果

| 检查或命令 | 实际结果 | 证据类型 |
| --- | --- | --- |
| git worktree add -b codex/experiment-image-source D:/code/git-project/powermem/powermem-image-source-experiment 6b2f6e8a4c1aac59fca75662aca26d78748bfa29 | 成功创建同级 worktree 和本地分支 | Git 实际执行 |
| git branch --show-current 与 git rev-parse --show-toplevel | 分支和根目录与建设指南一致 | Git 实际执行 |
| PowerShell 严格 UTF-8 解码、内部链接和空白检查 | AGENTS.md 与两份实验文档通过，内部链接目标存在，代码围栏平衡，无行尾空白 | 文档检查 |
| 阶段标题检查 | 阶段零至阶段七齐全 | 文档检查 |
| git diff --check | 通过 | Git 差异检查 |
| 实验 worktree 的 git status --short | 仅 AGENTS.md 修改和 docs/experiments/ 新文档 | Git 实际执行 |
| 主 checkout 的 git status --short | 仍仅有原来的未跟踪 docs/my/，实验说明未写入主 checkout | Git 实际执行 |

本次没有运行 make check、业务 pytest、模型探测或真实宿主图片测试；只有建设文档与 AI 执行约束变更。

### 限制与下一步

没有执行真实图片上传、私有视觉模型调用或图片功能业务测试。阶段零仍为待开始。

下一步是在独立实验环境中验证 Codex CLI 与 Desktop 的真实图片输入，并用用户提供的私有模型连接配置验证候选接口。可分别推进这两个验证，依据结果创建 feasibility.md；不预先宣称宿主图片自动捕获可用。

## 2026 年 10 月 7 日 阶段零与阶段一建设

### 目标与前置条件

完成公开资料、现有代码、宿主附件入口的可行性分析，以及可检验的数据和操作契约。工作目录与分支不变。用户明确选择本轮保留两个内测模型的真实验证待补，因此不读取主 checkout 凭据，不调用私有模型，不把公开模型或模拟服务作为内测型号的验收替代。

### 本次工作计划

- 核对官方图片 API、Codex hook 与实际安装版本，尽可能在独立目录验证真实 CLI 的附件处理。
- 建立宿主和模型能力矩阵，固定后续真实验证方法与限制。
- 冻结原图与解析 Source 的身份、来源、投影、任务状态、配置和资源预算。
- 核对 OpenAPI 生成与 route、MCP、Access 的一致性后添加必要契约代码与检查；本轮不实施阶段二的资产服务或阶段三的后台推理。
- 同步建设指南和实施记录，交接前补齐实际命令、结果与未完成验收。

### 分析结论与实际变更

- 创建 feasibility.md，固定能力矩阵、价值边界、候选模型验证方法与首条显式上传路线。
- 创建 codex-capture-evidence.md，记录 0.141.0 真实 CLI／app-server、自制 PNG／JPEG 和 localhost 模拟 provider 的证据。UserPromptSubmit 没有附件且当前图片尚未入 transcript；普通 Stop 时可定位原路径。大 PNG 被宿主缩到 2048×1365，不能把模型输入字节冒充原图。
- 创建 contract-integration-analysis.md，核对 Source 注册、子进程、原图准入、Topic Memory、证据回溯、pending 水位、权限和生成器的接入点。代码分析不计为运行时验收。
- 创建 contracts.md，固定两个 Source 类型、版本 1、五个操作、canonical 身份、配置快照、自动／手动重试差异、存储事务、权限、初始预算和后续校验边界。
- 更新 README.md，链接阶段成果，明确未激活操作、两个稳定类型、原图过滤、不可读结果、重试语义与首版不缓存模型结果的范围。
- 在 openapi/powercontext.yaml 中加入实验 schema 和 x-powercontext-experimental-image-source 未激活操作声明；标准 paths 没有新增图片项，默认 MCP 白名单和调用操作表未修改。
- 实际运行生成器更新 http/_generated/models.py、schema.py，并在 http/__init__.py 导出 21 个传输类型；没有手工编辑生成文件，没有改既有 SourceRef。
- 添加 builtin/images/identity.py 的 RFC 8785 摘要与原图 occurrence 身份函数，以及 test_experimental_image_contract.py 的离线契约测试。没有添加图片接收路由、注册运行时 Definition、队列表、模型连接或安装向导。
- 在独立 worktree 执行 uv sync --locked，使用 Python 3.14.5 创建 .venv；uv.lock 和依赖声明没有变更。探测材料保存在忽略目录，临时模拟服务与 app-server 均已关闭。

### 验证结果

本机没有 make 命令，使用 Makefile 对应的 uv 命令执行检查。以下结果按最终文件状态记录；没有把类型检查的 Linux 目标设置称为在 Linux 上实际运行。

| 检查或命令 | 实际结果 | 证据类型 |
| --- | --- | --- |
| 隔离 probe.py、--formats、--api | 真实 CLI 文字加图、同文异图、多图、大 PNG、小 JPEG 成功发送；exec 空 stdin 图片-only 被拒绝；app-server 两种图片-only 输入成功，但 Hook 未执行 | 真实 CLI／app-server + 模拟 provider，无真实识图 |
| uv run --locked python scripts/generate_api.py | 成功生成实验 DTO 与 canonical schema | 实际契约生成 |
| generate_api.py --check 与 generate_js_operations.py --check | 通过；JavaScript 操作文件无需变更 | 契约检查 |
| pytest tests/test_experimental_image_contract.py tests/test_api_contract.py tests/test_js_operations.py tests/test_mcp.py -q | 73 passed，53.54 秒 | 离线契约与既有 HTTP／MCP 行为测试，无图片运行时验收 |
| pytest tests/test_integration_manifest.py -q | 30 passed，8.36 秒 | 现有集成能力声明测试 |
| uv lock --locked | 通过，259 个包，无锁文件变化 | 依赖检查 |
| release_version.py --check | 通过，发布引用仍为 1.2.0 | 版本检查 |
| check_workflow_actions.py .github/workflows .github/actions | 84 个 action 引用通过 | 工作流检查 |
| generate_integration_manifest_docs.py --check | 通过 | 既有生成文档检查 |
| ruff check 与 ruff format --check，定向覆盖 http 导出、images 与新增 tests | 通过，四个手写 Python 文件已符合格式 | 代码静态检查 |
| ty check 定向覆盖 http 导出、全部 http/_generated、images 与新增 tests | 通过 | Windows 上的新增契约静态类型检查 |
| uv run --locked ty check | 失败：未修改的 native code 模块和测试使用 O_NOFOLLOW、fcntl、resource、mkfifo 等 Unix API，32 条诊断 | Windows 全库检查限制，不能记作通过 |
| uv run --locked prek run -a | 前十个 hook 通过；ty-check 因同一 Windows 全库问题失败 | 全库 hook 检查；未跟踪新文件另做定向检查 |
| uv run --locked ty check --python-platform linux | 通过 | 在 Windows 运行、按 Linux 目标解析类型；不是 Linux 运行时验收 |
| uv run --locked ty check integrations/pydantic-ai/src | 通过 | 既有 integration 类型检查 |
| git diff --exit-code -- src/powercontext/builtin/code tests/builtin/test_native_code.py src/powercontext/cli/opencode.py | 无差异 | 确认全库诊断涉及的文件本轮未修改 |
| 严格 UTF-8、metadata、内部链接、围栏与空白检查 | 六份实验文档通过，81 个本地链接目标存在 | 文档检查 |
| git diff --check | 通过 | Git 差异检查 |
| 主 checkout git status --short 与 HEAD | 仍只有原有 ?? docs/my/，HEAD 不变 | 主工作区隔离检查 |

新增测试初次暴露了测试断言的问题：生成的数组项使用 RootModel 包装，FastAPI 将带 $ref 的请求解析为 Schema 而非唯一的 Reference 类型。测试改为检查序列化后的可观察值，并正确收窄 schema 类型；未放宽生产 schema 或改变现有行为断言。最终 73 项测试通过。

Windows 全库 ty 诊断均在本轮未修改的代码／测试中；相同全库以 Linux 类型目标检查通过。没有为实验绕过或降低既有 ty hook，没有修改 Unix 专用实现。阶段一的生成与定向检查通过，全库 Windows check 仍保留明确失败记录。

### 未完成验收与后续约束

真实 MiniCPM-V-4.7-1B、MiniCPM-V-4.7-35B-A3B 的 endpoint、精确服务 ID、结构化输出、质量、用量和延迟均待补。本轮没有私有模型请求。Desktop 上传／粘贴、交互 TUI、Interrupt、异常退出、临时原文件寿命，以及图片-only Hook 也未验收。

尚无新图片 HTTP 接收、PNG／JPEG 服务端解码、资产持久化与回读、任务恢复、文字投影、Memory／Topic Memory 图片消费或跨会话召回的运行证据。实验功能仍不可启用；不能把字段校验和模拟 provider 计作以上行为已实现。

阶段一通过后，下一步是阶段二：在独立 SQLite 实验部署中接入被动原生 Definition、原图资产和任务接收事务，激活 capture、asset/get、task/get 三个已实现且经授权的操作，补齐 Client／CLI，并验证实际 HTTP roundtrip、原文件删除、重放／冲突、失败恢复与预算拒绝。先完成这条原图链路，再进入阶段三视觉处理；暂不开启 Codex 自动采集。

本轮没有 commit、push、PR 或发布。实验 worktree 和分支保持可继续开发状态，主 checkout 未修改。

## 2026 年 10 月 7 日 阶段二建设与代码审查

### 目标与前置条件

实现默认关闭的 PNG／JPEG 显式摄取、受管理资产保存、原图 Source 和 pending 任务同事务接收、HTTP／Client／CLI 回读与查询。沿用阶段一固定契约、独立 SQLite 环境和资源预算。真实模型及宿主自动捕获仍在后续阶段，不读取主 checkout 的私有数据。

### 本次工作计划

- 核对当前运行时、数据库、Access、Client 和 CLI 的实际接入边界。
- 实现原图类型、格式校验、资产持久化、接收幂等、pending 任务快照、格式兼容与预算控制。
- 激活 capture、asset/get、task/get，沿用现有授权；补齐 Client 与显式 CLI 上传／下载。
- 验证实际 SQLite 与 HTTP 链路、重启、原文件删除、冲突、损坏、关闭与越权行为。
- 使用 Open Code Review 审查本轮完整工作区改动，修复有效问题并重跑相关验证；记录实际结果和后续步骤。

### 实际变更与取舍

- 添加 builtin/images/config.py、models.py、tables.py、service.py，将两种被动图片 Definition 注册到共同内置 registry。原图和本阶段尚无消费投影的解析类型均排除在生成准入之外；普通 Artifact worker 能解码历史类型，不会读取原始图片作为文字证据。
- ImageConfig 默认关闭；增加独立 Vision 目标和非秘密配置快照。只声明接收时的模型身份，不读取私有凭据、不探测 endpoint、不调用模型。images extra 使用 Pillow 12.3.0；没有 Torch、CUDA 或模型权重。
- 严格验证真实静态 PNG／JPEG、完整解码、MIME、8 MiB 字节数、20,000,000 像素及单边 16,384。HTTP 在 JSON 解析前限制 12 MiB 请求体，包括无 Content-Length 的分块输入。
- 资产先耐久预留、后发布；同一 SQLite 接受事务写 retained 状态、原图 Source、pending 任务、Source 通知和幂等响应。相同请求返回原 sequence 与 task_id，改变内容同键返回 409；Scope 内资产去重，跨 Scope 独立计费与授权。
- 相同字节的失败上传可复用 reserved 资产；预留持续计费，未被接受的资产不能回读。启动维护只按索引清理至多 100 个超过 24 小时的孤立预留，保留 retained 原图；恢复旧预留会刷新宽限期。
- 使用固定的资产临时文件名，使索引能定位中断残留；清理完整文件和临时文件后才释放预算。POSIX 同步新建目录、发布和清理的目录项；Windows 使用带 write-through 的原子发布。取消时等待文件线程结束再释放写事务。
- 限定文件 SQLite、WAL／DELETE journal 和 FULL 及以上 synchronous。内存数据库及不耐久配置拒绝图片接受，避免“保存成功”在重启后丢失来源；普通文字保持既有行为。
- 将 capture、asset/get、task/get 移入唯一 OpenAPI 标准 paths，实际运行生成器更新 Python 和三份 JavaScript 操作表。retry、reparse 仍未激活，默认 MCP 白名单未增加图片操作。
- 补齐 Client 三个异步方法及 CLI image upload／task／download。任务正式读路径包含 exact original_payload；下载验证摘要和字节数，原子创建新文件并拒绝覆盖已有目标。
- 新增 phase2-usage.md，同步指南、契约、可行性和接入说明。没有重构现有 SourceRef、数据库迁移、安装向导、记忆产品界面或宿主采集代码。

### Open Code Review 与修复

实际使用用户指定的 [Open Code Review 插件](https://github.com/alibaba/open-code-review)，通过 `ocr review --audience agent` 审查本地工作区。provider 为 deepseek、model 为 deepseek-flash；会话 9cb7c058-1588-4153-8e35-9ced8916af05 从 10 月 7 日 23:54 到 10 月 8 日 00:11，结果为 partial：20 个选择项中 15 个完成，5 个运行时接入文件超时，提出 6 条意见。没有将退出码 0 或 partial 报告计作全部模型审查通过。

随后实际运行同插件的 `ocr delegate preview --format json` 和 `ocr delegate rule --format json`，按规则补审超时文件、新增共享上下文模块和修复后的代码。最终清单有 21 个可审查文件，全部有审查结论，无未检查的选中项。生成文件、锁文件、指南及测试未进入模型清单；分别通过生成一致性、依赖、文档检查和实际测试验证。委托补审由当前编码 Agent 执行，属于插件委托审查，不是第二次外部模型审查。

| 审查发现 | 处理与验证 |
| --- | --- |
| 上下文读取检查丢失可信分组与 actor | 抽取共享 AccessAuditContext 构造，图片和既有 HTTP／MCP 操作复用。分组 contributor 携带文字引用的上传从实际 403 修复为 202；测试显式启用 provider 的 group 能力，不宣称静态 bearer 默认支持分组 |
| ImageError 未进入统一日志与 tracing 映射 | 保留实际状态与 error_code；409／404 从 internal_error 和 ERROR 修复为图片错误码和 WARNING |
| CLI 请求校验误报为文件错误 | 分离 ValidationError；空 capture_key 以请求错误退出，不提示修改文件 |
| CLI 下载完整性错误误报为输出路径错误 | 分离数据校验失败与本地保存失败；损坏响应不会提示更换 output |
| Base64 上限重复写字面量 | 从原始字节预算计算共享常量，HTTP 预检查和领域解码使用同一值 |
| 接收路径存在第二条资产插入／计费分支 | 收拢为预留后的 retained 转移；没有有效预留时拒绝接受，恢复旧预留刷新宽限期 |
| 补审：缺少状态字段导致整个服务无法启动 | 在读取实验状态前检查全部表与字段，图片返回格式不可用，普通文字可写 |
| 补审：维护文件错误导致整个服务无法启动 | 清理错误只停用图片能力，记录有界错误码；故障注入验证文字可用 |
| 补审：内存 DB／MEMORY journal 接受了耐久上传 | 验证实际 SQLite 文件与同步配置，接收前拒绝；真实 API 测试验证不生成资产 |
| 补审：崩溃临时文件未随预留清理 | 固定临时文件身份并纳入清理；模拟中断文件状态后真实重启，磁盘原始资产字节从超预算恢复为预算以内 |

模型意见均已处理；补审没有遗留阻塞问题。这里保存可检查的发现与结果，不保存模型内部推理。

### 验证结果

环境为独立 worktree、Windows、Python 3.14.5、Pillow 12.3.0、实际 SQLite 文件。没有 make，使用 Makefile 对应 uv 命令。最终代码与文档在 10 月 8 日完成。

| 检查或命令 | 实际结果 | 证据类型 |
| --- | --- | --- |
| pytest tests/e2e/test_image_source_product.py tests/test_experimental_image_contract.py tests/builtin/persistence/test_sources.py -q | 46 passed，31.80 秒；其中图片验收 24 项 | 实际 SQLite／HTTP adapter、实际 loopback Uvicorn／CLI／Client；契约与 Source 兼容检查 |
| pytest 图片验收、test_server_logging.py、test_access_http.py、test_access_adapters.py、test_access_mcp.py、test_mcp.py -q | 64 passed，53.68 秒；在公共授权上下文修复后执行 | 既有 HTTP／MCP／日志和图片回归 |
| pytest 实验契约、API、JS、MCP、processing_source_registry、generation_sources | 88 passed，58.71 秒 | 契约、操作声明、子进程 registry 与消费准入回归 |
| pytest 既有 Sources、CLI、Client 和 Topic Memory processing | 121 passed，70.41 秒 | 既有文字、客户端和处理链路回归 |
| pytest tests/test_integration_manifest.py -q | 30 passed，6.22 秒 | 集成能力声明兼容 |
| 分组权限、日志、缺字段、维护错误、非耐久 DB 与中断文件残留回归 | 修复前出现对应失败，修复后全部通过 | 实际 API／DB；文件状态与维护错误明确使用模拟或故障注入 |
| generate_api.py 与最终 --check，generate_js_operations.py 与 --check | 通过；生成文件均由工具产生 | 唯一契约与生成一致性 |
| uv lock --locked | 通过，260 个包 | 可选 images 依赖及锁文件一致性 |
| release_version.py --check、check_workflow_actions.py、generate_integration_manifest_docs.py --check | 通过，版本 1.2.0，84 个 action 引用 | 既有项目检查 |
| 手写新增／修改文件 ruff check --no-fix、ruff format --check | 通过 | 新增文件不依赖 Git 跟踪覆盖的定向静态检查 |
| uv run --locked prek run -a | 前十个 hook 通过；ty-check 失败，32 条既有 Windows Unix API 诊断 | 全库 hook 检查，不能记作全绿 |
| uv run --locked ty check --python-platform linux | 全库通过；新增代码定向 Windows 类型检查通过 | Windows 执行的静态检查，不是 Linux 运行验收 |
| uv run --locked ty check integrations/pydantic-ai/src | 通过 | 既有 integration 类型检查 |
| 文档 UTF-8、metadata、内部链接、围栏与空白；git diff --check | 通过 | 最终文档与 Git 检查 |
| 主 checkout 状态和 HEAD | 保持起始 HEAD，仅有原来的未跟踪 docs/my/ | 实验隔离检查 |

Open Code Review 模型报告、最终选择清单、规则和委托覆盖摘要位于忽略目录 `.powercontext/image-source-experiment/phase2/`。没有把累计或重复运行的测试次数作为独立场景数。全库 Windows 类型限制仍来自未修改的 Unix 专用模块和测试，没有降低检查或修改无关代码。

### 未完成事项与下一步

阶段二四项验收成立：接受后可回读精确原图，重复请求保持来源身份，失败不会返回已接受但不可读的 Source，关闭时拒绝新摄取。历史图片在关闭和缺少模型配置时仍有受权限保护的回读路径。

pending 任务会保留至阶段三处理，本轮没有 running claim、模型请求、解析 Source 生成、自动重试或重新解析。新图片不能检索为 Memory，Codex／其他 Agent 自动接图和配置向导也未建设。私有模型、真实宿主、Linux 运行及实际硬断电恢复没有验收；中断残留测试只模拟崩溃后的文件状态，再执行真实重启。

下一步按阶段三接入独立 Vision 适配、固定提示与预处理、严格输出校验、有界 worker 和 claim 恢复；真实模型配置仍由用户后续提供。工作区和分支保持不变，本轮未 commit、push、创建 PR 或发布，主 checkout 未修改。

## 2026 年 10 月 8 日 真实模型配置入口准备

### 目标与前置条件

用户准备填写真实 MiniCPM-V 配置。核对实际环境文件加载、Vision 字段与 SecretStr header，提供忽略目录内的独立配置模板；本次不开始视觉 worker 编码，不请求真实服务，也不读取主 checkout 凭据。

### 工作计划

- 在 .powercontext/image-source-experiment/minicpm.env 创建未配置凭据的本地模板，不覆盖已有文件。
- 固定实验 POWERCONTEXT_HOME、文件 SQLite、资产目录和端口，保持图片开关关闭。
- 使用现有配置解析器检查模板及模拟鉴权字段，检查 Git 忽略规则，再提供可编辑入口。

### 实际结果与下一步

- 已创建忽略目录中的 minicpm.env，采用独立 POWERCONTEXT_HOME、runtime.db、资产目录和 18171 端口。真实模型 ID、endpoint 与鉴权内容为空，部署标识给出可编辑的本地实验值，图片开关保持 false。
- 文件以排他创建方式写入，不覆盖用户已有配置；没有修改生产代码、读取主 checkout 配置或启动服务。
- 使用 read_environment_file 和 server_settings_context 实际加载模板；用 .invalid 示例 endpoint 和假 Bearer header 验证 SecretStr JSON 字段及 parse_profile，全部通过，无网络请求。
- git check-ignore 确认 minicpm.env 被忽略；git diff --check 通过。本次仅提供配置入口，未执行真实模型或业务 pytest。
- 用户填写本地连接资料后，再按阶段三实现视觉解析、任务恢复并进行真实接口验证；凭据不进入聊天和实施记录。

## 2026 年 10 月 8 日 面壁 35B 候选连接配置

### 目标与前置条件

用户指定 MiniCPM-V-4.7-35B-A3B，要求查证面壁相关连接字段并配置，API Key 由用户填写。仅修改忽略目录中的 minicpm.env 与实施记录，不调用模型、不变更默认开关或建设阶段。

### 工作计划与资料依据

- 使用 web-access 完成联网前检查，检索并读取 OpenBMB 的官方 API Guide。
- 按 [官方 API Guide](https://github.com/OpenBMB/MiniCPM-V/blob/main/docs/api.md) 填写通用 base URL https://api.modelbest.cn/v1，采用 Chat Completions 与 Bearer 鉴权。
- 模型 ID 使用用户明确指定的内测值；官方公开列表仅列 4.5／4.6 等型号，不据此声称 4.7 的账号权限或实际调用通过。
- 更新模型、地址和本地部署标识，准备 API Key 占位 header；保留可能已有的鉴权内容，不回显凭据。静态加载验证后交给用户填写 Key。

### 实际结果与下一步

- minicpm.env 已设置模型 MiniCPM-V-4.7-35B-A3B、base URL https://api.modelbest.cn/v1、本地部署标识 modelbest-minicpm-v-4.7-35b-a3b-private-v1。
- 鉴权配置为 Authorization 的 Bearer 占位值，YOUR_API_KEY 由用户在本地替换。该部署标识是本地实验标记，不代表面壁公布了对应权重版本。图片开关及独立存储配置保持不变。
- 首次静态校验把 Pydantic URL 对象与字符串直接比较，断言失败且尚未写入文件；改为核对 JSON 序列化值后，环境文件解析、ServerSettings、SecretStr header 与 parse_profile 全部通过。
- 检索及官方原文核对证明通用接口地址、Chat Completions 和 Bearer 方式；无法以公开型号清单证明内测 4.7 的账号授权。平台首页未能通过网页读取工具访问，未访问账号控制台、读取凭据或发送模型请求。
- 模型 Key 填好后进入阶段三并验证实际接口，不将配置可加载记录为真实识图通过。本次未执行业务 pytest，未修改生产代码、commit 或推送。

## 2026 年 10 月 8 日 阶段三与阶段四建设

### 目标与前置条件

用户已填写独立 minicpm.env，授权先验证真实 MiniCPM-V 配置，验证成功后完成阶段三视觉解析与任务恢复、阶段四文字证据消费与回溯。使用自制测试图片，不读取主 checkout 数据、配置或已有服务；凭据仅在本地加载，不回显。

### 本次工作计划

- 用有界真实单图请求验证连接、授权、图片输入和严格结构化结果；保存脱敏用量与耗时，明确没有验证的能力。
- 按固定提示与预处理版本实现独立 Vision 适配和本地输出校验，关闭隐式重试，记录每次实际 provider 调用。
- 增加有界、按 Scope 公平的图片 worker，任务 claim／revision、临时失败退避、重启恢复和旧配置保护；激活 retry／reparse，保持旧来源不变。
- 实现解析 Source 的稳定文字投影、生成准入、Topic Memory 消费、迟到结果通知、原图 lineage 和同原始证据归组。
- 用 SQLite／HTTP acceptance 验证重启、迟到结果、显式重试、重新解析、权限、Memory 召回与精确来源回读；真实模型验证与模拟错误场景分别记录。
- 运行契约、focused 检查和相关回归，复核改动并更新指南、契约与实施记录。宿主自动捕获和安装向导不属于本轮范围。

### 实际实现结果

- 阶段三已完成独立 Chat 多模态适配。固定提示、六字段 schema、EXIF 方向处理与元数据剥离均有明确版本；严格拒绝无效 JSON、重复字段、截断、超预算和不存在的实体引用，不尝试隐式修复。原图与实际推理输入分别保存摘要，HTTP 客户端不自动重试或跟随重定向。
- 图片 worker 独立于 Artifact family。SQLite owner、执行租约、revision 与 claim fencing 协调单个有效 worker，按 Scope 轮转，有界恢复遗留 running。429、5xx、超时和断连在原尝试预算内退避；成功 Source、终态、容量释放和 Topic Memory 通知在同一事务提交。正常关闭停止领取任务并完成当前有界操作；被终止的进程通过租约到期恢复。
- retry 和 reparse 的 HTTP、Client、CLI 路径已激活，并同时要求 Scope 读取与贡献权限。两者都有独立幂等键；retry 固定旧配置，reparse 固定当前配置，保留历史任务与解析。换模型或 endpoint 后，旧任务无法使用不匹配的新凭据，不发生静默切换。
- 每次尝试记录实际请求、输入摘要、延迟、HTTP 状态及已报告的 token；中断后不确定的请求数和 token 保持 null。统计增加 image_parsing 分类，任务审计记录与尽力写入的运营统计分别说明。
- 已知阶段二 SQLite 格式使用显式写事务添加生命周期字段及索引，保留原 Source、资产、任务与回执；故障回滚后可继续启动。未知或不完整格式仍明确停用图片能力。任务与逐次尝试通过真实读事务保持同一快照。
- 阶段四已完成无模型调用的固定文字投影。原图和 unreadable 解析排除于生成；可读解析用于 Memory、Handoff 及 Topic Memory 的共同证据入口。解析完成时的新 sequence 和水位通知让已推进原图窗口的消费者看到迟到结果。
- EvidenceResolver 保存解析到原图的 lineage，按受管理原图中的 Scope 和原始资产摘要归组。同图的不同来源、重试与重新解析各自保留精确记录，但不成为多份独立佐证。既有 Artifact 的历史解析引用保持不变，沿途执行权限检查。
- 指南、固定契约、接入分析、可行性矩阵和阶段二入口已同步当前状态；新增 [阶段三、四使用说明](phase34-usage.md)。图片开关保持默认关闭，用户填写的 minicpm.env 仍为 false，凭据和运行证据全部留在忽略目录。

### 真实模型与跨会话验证

连接使用用户填写的 MiniCPM-V-4.7-35B-A3B 配置。测试图为自制 960×540 中文报错截图，包含 IMG-034、E429 和连接失败文字，不包含私人资料。使用实际私有 provider 请求验证鉴权、图片输入、中文 OCR 和严格 JSON，不用静态配置可加载代替模型验收。

部分早期实际响应为 HTTP 200，但在 OCR 字符串内输出非法换行，被严格校验拒绝，原图保留且任务失败。将固定指令与图片放在同一个 user 多模态输入中、明确要求单行 OCR 后，生产适配和实际 worker 成功，不放宽校验或修补响应。失败记录保留，最终成功使用显式新任务。

最终实际 worker 单次尝试成功：provider_requests 为 1，输入 1133 token、输出 200 token、模型请求 2133 毫秒，上传至回读验证 3.349 秒。IMG-034 与 E429 识别正确，解析引用和原始字节回读一致。部署标识为本地标签，不能证明固定权重；以上数据仅代表该样本，不能作为总体成功率或延迟基准。

同一真实解析结果进入受控 Memory 生成链路后，重新启动服务、关闭视觉模型和普通生成器，通过 FTS 查询 IMG-034 E429 找到一条记忆；沿 Artifact 引用读回完全相同的解析 payload 和原图身份。这次回读没有再次调用模型。普通 Memory、Handoff、Topic Memory 生成器是确定性测试实现，结果证明消费、存储与引用闭环，不代表生成质量验收或真实 Codex 新会话操作。

脱敏调用摘要、测试图与本地脚本位于忽略目录 .powercontext/image-source-experiment/phase34/。API Key、请求 header 与完整私有响应没有进入代码、文档或公开日志。

### 代码审查与修复

本轮使用 [Open Code Review 委托模式](https://github.com/alibaba/open-code-review/blob/main/skills/open-code-review-delegate/SKILL.md)，OCR v1.12.12 负责选择文件和解析规则，由当前 agent 按 REVIEW.md 审查。没有向额外 OCR 模型服务发送代码，也没有发布评论。

全部 28 个选中的手写生产文件均已复核；另检查六个图片测试文件和八份实验文档，生成文件通过 canonical generator 与契约测试验证。最终清单、规则、逐文件状态及 SHA-256 覆盖记录保存在 phase34 的 review-selection-final.json、review-rules.json 和 review-coverage.json，没有跳过选中文件或未处理的阻断问题。

- 修复 SQLite legacy transaction control 下 SELECT／DDL 没有开始真实事务的问题。初始化显式 BEGIN IMMEDIATE，任务回读显式 BEGIN；迁移故障注入验证完整回滚和下一次历史回读。
- 将 30,000 层嵌套、仍在字节预算内的非法 JSON 的 RecursionError 归为明确解析失败，避免停止 worker。提供方 envelope 同样处理该错误；不放宽结构校验。
- 修复立即取消 worker 的数据库操作可能在重开服务时留下锁的问题。worker 和 heartbeat 均使用协作关闭，不取消正在执行的 SQLite 操作；当前网络调用仍受有限超时约束。原图孤儿恢复、双 runtime、当前任务完成与待处理队列保留检查通过。
- 对缺少可选图片解码依赖给出有界任务失败，保留冷启动 Source 注册与读取；配置保护、跨 Scope 读取和重试／重新解析权限路径一并复核。

### 实际检查记录

下列命令均在实验 worktree 执行。make 在本机不可用，使用 Makefile 对应的 uv 命令；不将重复执行的通过数累计成独立场景数。

| 检查 | 最终结果 | 证据边界 |
| --- | --- | --- |
| 真 provider 探测、real_worker.py | 35B 实际解析、持久化及精确原图回读通过 | 自制单图与真实远程模型 |
| real_memory.py，以 runpy 在项目路径执行 | processed_source_count 1，重启后 FTS 命中 1，旧解析与原图身份一致 | 真实 Vision 输出、受控普通生成器；没有新网络调用 |
| pytest test_image_vision、test_experimental_image_contract、四个 image e2e、test_api_contract、test_js_operations -q --tb=short | 115 passed，160.57 秒 | 最终图片、API／JS 契约及恢复验收 |
| pytest 新增重试／重新解析拒绝访问的 Scope authorization 场景 | 1 passed，9.04 秒 | 最终补充权限断言，已包含于上述文件；不重复计数 |
| pytest generation_sources、processing_source_registry、statistics、statistics_flow、handoff_runtime、reviewed_generation、artifact_dreaming、topic_memory_processing、access HTTP／adapters／MCP、MCP、server_logging | 118 passed，36 skipped，142.95 秒 | 既有文字与生成流程回归；跳过项保持未验收 |
| 实际子进程终止、等真实租约到期后重启 | interrupted 与 succeeded 两次尝试，只有一个有效解析 | 实际本地进程／SQLite／受控 HTTP provider；不模拟进程死亡 |
| 迁移中断、陈旧响应与 claim 过期 | 全部通过 | 明确的故障注入；没有声称真实硬断电 |
| generate_api.py --check、generate_js_operations.py --check | 通过 | HTTP／JS 生成一致性，不手改生成文件 |
| uv lock --locked、release_version.py --check、check_workflow_actions.py、generate_integration_manifest_docs.py --check | 通过；260 个包、版本 1.2.0、84 个 action 引用 | Makefile 对应项目检查 |
| 32 个新增／修改手写 Python 文件 Ruff check／format --check | 通过 | 包括未跟踪的新模块和测试 |
| 修改文件定向 Windows ty check、全库 Linux-target ty check、Pydantic AI integration ty check | 通过 | 实际在 Windows 执行的静态检查，不是 Linux 运行验收 |
| uv run --locked prek run -a | 前十个 hook 通过；ty-check 失败，32 条既有 Windows Unix API 诊断 | 全库检查不能称为全绿，未修改无关平台代码 |
| 文档 UTF-8、metadata、内部链接、围栏、空白与 git diff --check | 通过 | 最终本地文档与 Git 检查 |
| 主 checkout 的 HEAD 与状态；配置、DB、测试证据的忽略规则 | 原 HEAD 不变，仅原有 docs/my/；私有实验材料均忽略 | 工作区隔离，没有 commit、push、PR 或发布 |

最初 consolidated 检查为 112 passed、2 failed：一项为关闭时 SQLite 锁，另一项为测试使用不存在的健康路径。锁问题已修复并增加正常关闭验收；健康检查改用实际 /health/live，并验证图片迁移失败时正常 Scope 写入仍可执行。中间执行曾使用不存在的测试节点或从脚本目录导入 tests 失败，修正命令后完成上述最终验收，未把这些失败记作功能通过。

### 阶段状态与下一步

阶段三的真实模型解析、错误保留、有限重试、恢复 fencing、禁用后不领取任务均已验收。阶段四的新 runtime 文字检索、精确解析和原图回读、不可变旧引用、模型离线可读及迟到消费均已验收。阶段三与阶段四完成。

阶段五至七尚未建设。下一步仅对已探测版本的 Codex CLI 普通 Stop 做可选、有界的附件适配，并进行真实宿主验收；Desktop 自动上传／粘贴、MiniCPM-V-4.7-1B、安装引导、复杂图质量和实验收益评估继续待补。支持仍限定本地文件 SQLite，没有上游提交或部署承诺。

## 2026 年 10 月 8 日 阶段五与阶段六建设

### 目标与前置条件

用户已修复部分图片服务、任务、Vision、worker 和 HTTP 边界，要求在当前实现上完成 Codex 输入适配、配置引导与最小证据查看，随后调用 Open Code Review 并修复问题。本轮保留用户修改，继续使用独立 worktree、配置、SQLite、资产与端口，不修改主 checkout 或全局宿主配置，不提交上游。

### 本次工作计划

- 保存当前工作区文件摘要作为本轮基线，检查用户修改及已有阶段零证据，并核对官方 Hook 契约。自动适配限定已经验证的 Codex CLI 版本与文件附件输入；Desktop、粘贴和未知格式继续明确未支持。
- 增加默认关闭的普通 Stop 附件捕获入口，复用已有 Scope 绑定与鉴权，按明确的会话、轮次及附件位置构造身份。增量读取单个当前 transcript，限制读取、文件复制、HTTP 与总耗时，不同步等待视觉推理；重放和失败有明确诊断。
- 在既有配置向导增加可跳过的独立实验模块，配置 Vision 和资产目录，展示当前支持范围。静态验证与显式真实测试分开，不因普通记忆选项自动开启图片或上传数据。
- 在现有证据页面显示经授权读取的原图、任务、解析、不确定项和模型版本，并补充启用、关闭、重试、重新解析、备份与删除边界。保持既有文字页面可用，不另建素材管理产品。
- 执行真实隔离 Codex CLI 附件上传、宿主重放、服务重启和新会话召回验收；受控 provider、真实 MiniCPM-V 与直接 Hook 测试分别标注。补充向导、Dashboard 权限、转义与失败路径检查。
- 完成相关回归和契约检查后调用 Open Code Review，核查并修复有效问题，更新逐文件覆盖、使用说明、能力矩阵和本记录。阶段七只给出下一步，不提前宣称收益评估完成。

## 2026 年 10 月 8 日 阶段三、四专项代码审查

### 本次目标与计划

用户要求使用 Open Code Review 再次审查阶段三、四代码与逻辑，并修复发现的问题。在现有实验 worktree、分支和未提交状态上继续，保持默认关闭和上游隔离。

- 重新运行 OCR，按建设指南、固定契约与 REVIEW.md 检查后台任务、解析边界、引用、消费、权限和恢复；不把前次通过记录作为本次审查的替代。
- 核对 OCR 每条发现的实际调用及持久化路径，能复现的缺陷先保留行为回归，再实施最小修复；继续覆盖没有模型评论的文件。
- 执行相关图片和既有流程回归、契约生成与静态检查，保存完整本地审查结果和逐文件覆盖。
- 在本节记录实际发现、修复、最终检查及剩余限制；不提交、推送、扩大到后续阶段或暴露凭据。

### 审查范围与实际执行

本轮重新执行 Open Code Review v1.12.12 的模型审查，使用既有 deepseek-flash 配置，传入实验业务背景、固定契约和 REVIEW.md。代码、凭据及模型结果只按已授权的审查路径处理，没有改动 reviewer 配置或重新请求 MiniCPM。审查覆盖未提交工作区中的阶段三、四及其必要图片边界，生成文件和锁文件通过专门检查验证。

OCR 命令退出码为 0，但报告的状态为 partial：28 个选中文件中 18 个完成模型审查，10 个因上下文压缩或工具轮数限制未完成；共返回 5 条意见，耗时 25 分 16 秒。没有把退出成功等同于完整审查，也没有盲目重跑模型。未完成的十个文件使用 [Open Code Review 委托模式](https://github.com/alibaba/open-code-review/blob/main/skills/open-code-review-delegate/SKILL.md) 的选择与规则继续逐文件检查，包含公共契约、导出、统计、Runtime 组合、准入、Topic Memory、Source 注册和 EvidenceResolver。修复后再次检查全部 28 个选中文件，保存最终 SHA-256，补查六个图片测试文件和八份实验文档。

完整模型报告位于忽略目录 .powercontext/image-source-experiment/phase34-review/ocr-report.json；selection-final.json、rules.json 和 coverage-final.json 分别保存最终选择、规则及模型／委托覆盖。最终 reviewed_files 为 28，skipped_files 为 0，没有未处理的阻断问题；这不表示 OCR 模型单独完成了全部文件。

### 发现与修复

1. **鉴权配置异常停止图片队列。** 非 ASCII 的 header 值会逃逸为编码异常，使 worker 停止；非法名称及控制字符也没有被正确识别为前置配置失败。HTTP／SQLite 复现中第一任务转为 retry_wait，下一任务保持 pending，没有请求到达 provider。新增名称 token、控制字符和 httpx 自身编码预检，统一返回 image_vision_credentials_unavailable，每条任务花费一个前置尝试、provider_requests 为 0；worker 继续处理后续任务。三个回归在修复前失败、修复后通过。模型报告中“永久 running”的描述不准确，已按实际状态修正，队列停止这一问题成立。

2. **部署前缀绕过传输预算。** 中间件比较原始 ASGI path，非空 root_path 下的合法 capture 路由不匹配。实际 ASCII 和中文前缀下，超过 12 MiB 的分块请求返回 422，绕过约定的 JSON 前限制。改为使用 Starlette 的 get_route_path，与框架路由匹配方式一致；分块请求和超 Base64 字段均返回 image_too_large／413。两个前缀回归修复前失败、修复后通过，普通路径继续通过。OCR 将此项标为 low；本地按已经违反的传输预算认定为需要修复的行为问题。

3. **维护错误阻断历史证据，SQL 维护错误还可阻止普通服务启动。** 清理 reserved 孤儿失败会把整个 ImageService 标为不可用，导致已保存任务和 retained 原图无法读取。SQLAlchemy 维护异常没有被 optional capability 边界捕获。将存储格式可用性与新提交准入分开：维护失败时 capture／retry／reparse 返回 503，历史读路径保持正常，已接受任务可按原预算继续处理；未知格式、权限错误和资产实际损坏仍拒绝对应读取。IO、SQL 故障注入均验证精确原图与来源 payload 可回读、普通 Scope 可写；两个场景修复前失败、修复后通过。任务状态本身允许后台推进，不把 pending 状态冻结为历史证据。

4. **短暂 SQLite 锁需要人工重启 worker。** owner 获取或 heartbeat 续期遇到 BUSY 错误后，worker 不会再处理队列。按 SQLite 原生 BUSY／LOCKED 错误码分类，owner session 先完成清理再按 1 至 30 秒退避重新获取；续期暂时阻塞时每秒再试。有效 owner、claim、revision 和租约 fencing 保持原规则；失去 owner 后不能提交旧响应，未知错误与损坏记录不无限重试，关闭仍等待当前有界操作。两个故障注入场景修复前均超时，修复后无需重启即可完成两条任务，每条只调用一次 provider。

OCR 的另一条 low 意见建议删除 _reserve_asset 后对 self.profile 的检查。该检查保留接受事务边界的防御条件并为后续读取收窄可选类型，没有造成已证实的行为失败；按 REVIEW.md 不为风格意见进行无关重构。

### 最终检查

| 检查 | 实际结果 | 范围与限制 |
| --- | --- | --- |
| OCR 模型审查 | partial，18 完成、10 未完成、5 条意见 | 原报告与会话记录完整保留；未完成项由委托模式补齐 |
| 最终逐文件审查 | 28 reviewed，0 skipped；4 类行为问题修复 | 最终源码 hash 和意见处置分别保存，包含已修改边界的终审 |
| 新增 header／root_path／维护／数据库锁回归 | 修复前 3＋2＋2＋2 失败，修复后均通过 | 使用实际 HTTP、SQLite、本地 provider；故障明确注入，不声称真实生产故障或宿主验收 |
| pytest 六个图片测试文件及 API／JS 契约 | 124 passed，185.16 秒 | 包含原图、队列、进程终止恢复、fencing、文字消费与精确回读 |
| Access HTTP／adapters／MCP、MCP、日志、Server、generation_sources、processing_source_registry、Topic Memory 回归 | 115 passed，1 failed，85.94 秒；失败项单独复跑 1 passed，10.84 秒 | embedding MockTransport 就绪检查一次出现 timeout，保留原失败，未改实现或放宽断言 |
| tests/test_server.py 单独完整复跑 | 55 passed，30.75 秒 | 验证上述失败及 Server 生命周期、就绪、路由回归；不累计重复通过数 |
| Ruff check／format --check、修改文件 Windows ty、全库 Linux-target ty | 通过；覆盖 32 个手写新增／修改 Python 文件 | Linux-target 为 Windows 上的静态检查，不是 Linux 运行验收 |
| HTTP／JS generator --check、uv lock --locked、Pydantic AI integration ty | 通过，260 个包 | 公共传输 DTO 无新增变更，生成契约保持一致 |
| prek run -a | 前十个 hook 通过，ty-check 的既有 32 条 Windows Unix API 诊断仍失败 | 不把全库检查记为全绿，不修改无关平台代码 |
| UTF-8、metadata、链接、围栏、空白、git diff --check | 通过 | 最终八份实验文档与工作区检查 |
| 主 checkout、实验 HEAD 与私有材料忽略规则 | 原 HEAD 不变，主目录只有原来的 docs/my/；配置与报告均忽略 | 没有 commit、push、PR、发布或变更用户 minicpm.env |

最初独立 header 复现脚本在较长的实验子目录中触发 Windows 路径长度限制，尚未到达 header 逻辑；改用短路径的独立临时数据库和资产后得到上述有效复现。没有据此前置失败宣称 header 问题已验证。维护回归的初始断言要求可变任务状态保持 pending，修正为核对准确任务身份与不可变 original_payload，符合后台继续处理的契约。

### 状态与后续边界

阶段三、四在这次修复和回归后继续满足当前验收。默认开关、原图与解析身份、旧 Artifact 引用、Scope 权限和模型尝试预算保持既定规则；使用说明已补充维护隔离、锁恢复和部署前缀行为。没有新增宿主自动捕获、配置向导、模型权重管理或质量结论，阶段五至七仍待后续授权推进。

## 2026 年 10 月 8 日 阶段五与阶段六实施结果

### 实际改动

- Codex 插件增加默认关闭的 POWERCONTEXT_CODEX_CAPTURE_IMAGES。UserPromptSubmit 保存当前轮检查点，正常 Stop 增量读取经过版本校验的 CLI 0.141.0 exec 记录，按附件顺序提交原字节。Scope 使用既有绑定且关闭默认回退，提交与 Stop 之间检查 Scope 和 Server 一致。
- 捕获只处理当前会话，提交时最多检查 64 KiB 尾部，Stop 窗口最多 4 MiB，最多四张、每张 8 MiB。总处理预算默认 6 秒、最大 8 秒；宿主命令超时 10 秒，依赖启动另计。每张耐久接受后保存回执，同轮重放保留结果，不同步等待视觉解析或 Memory。
- 配置向导增加 --experimental-images 与 images 编辑模块，可跳过、独立配置 Vision、目录和 Codex 开关。已有普通引导不增加必答的模型项。静态校验与显式 image probe 分开；probe 仅发送一张自制图、一次请求，不保存 Source、资产或任务。
- Dashboard 通过现有 Scope 授权 API 回读精确任务和原图，不读取裸文件或图片表。新增 task_id 查看入口，现有 Memory／Artifact 材料链接打开精确解析 Source；展示原图、OCR、描述、不确定项、模型、部署标签及状态，明确区分接收、解析与记忆生成。
- Memory 材料链接携带完整历史 citation，桌面及移动布局共用既有证据弹窗；原图返回 no-store、nosniff，模型输出按文字转义。不可读结果仅保留供核查，不进入文字生成。没有新增图片公开 HTTP 操作、MCP 工具、素材管理、模型权重托管或单条删除。
- 新增 [捕获与查看说明](phase56-usage.md)，同步指南、契约、可行性矩阵、宿主证据和既有使用说明。服务图片开关与 Codex 开关仍为 false，未改用户 minicpm.env 或全局插件安装。

### 真实模型与宿主验收

显式 probe 使用当前私有面壁配置，真实 MiniCPM-V-4.7-35B-A3B 返回合格 schema，并识别图片中独有的 POWERCONTEXT-PROBE-7351。本次实际一请求，输入 625、输出 51 token，模型请求 1538 毫秒，source_saved=false。该样本证明连接与看图，不作为性能或复杂图片质量基准。

tests/e2e/test_image_codex_live.py 实际执行 Codex CLI 0.141.0 和发布配置中的 uv Hook 命令，使用隔离 CODEX_HOME、SQLite、资产目录和临时端口。第一轮上传 PNG，第二轮同文按顺序上传 PNG＋JPEG；后台识图、原图精确回读均成功。随后关闭图片接收、重启服务、删除上传源 PNG，再由新 CLI 会话获取已保存的相关记忆，沿 citation 读回同一解析和原图身份。

最终真实 CLI＋实际 35B 验收为 1 passed，53.88 秒；纯本地受控 Vision 的同链路为 1 passed，54.32 秒。Agent Responses provider 和普通 Memory 生成器由本地受控组件提供，不使用用户 Codex 账号模型，因此不宣称普通生成模型的质量或真实生产对话效果。临时 Hook 信任绕过仅限验收进程，不改变全局信任。宿主同轮重放、空文字、原图消失或修改、未知版本、超窗和超时分别用受控 Hook 输入验证，不当作对应真实宿主事件已验收。

真实 CLI 联调确认 turn_context 有时先于提交 Hook 启动写入，检查点据此改为有界尾部定位；接收成功按实际契约 status=accepted 判断。Windows 的 Hook 命令和图片参数分隔使用实际可运行方式，并用最终发布命令再次验收。调试包装已从最终测试移除。

### Open Code Review 与修复

实际调用 Open Code Review 模型审查，传入实验背景、AGENTS.md、REVIEW.md 和固定契约。结果为 partial：43 项中 7 项完成、36 项因超时或 provider／subtask 错误未完成，未返回有效意见。原始完整报告保存在 phase56/ocr-review.json，没有把命令退出 0 当作完整审查，也没有盲目重试。

随后使用同一插件的委托模式完成文件选择、规则解析与逐文件审查。最终 47 项均 reviewed、0 skipped，覆盖率 100%；包括最后补齐的 Memory 和模板入口。最终 SHA-256、覆盖与意见处置保存在 phase56/review-final.json。测试、九份实验文档另行核查；生成文件和锁文件通过专用检查验证。未保留待修复的高／中等级问题。

| 确认问题 | 修复与有效证据 |
| --- | --- |
| 原文件在 lstat 后、open 前同长度改写，可被错误接受 | 增加打开后 descriptor 与打开前属性的完整比较；原失败回归 1 failed、修复后 Hook 12 passed。深层 JSON 同时做失败兼容加固 |
| 现有 Artifact 列表过滤图片解析，Memory 缺少材料引用且 evidence origin 不接受 notes | 统一支持的引用视图、增加 Memory 链接及完整历史 citation、开放 notes 证据读取；实际 SQLite／HTTP 回归从缺失链接到通过，重新解析后旧 Memory 仍读旧解读，替换成未引用 Source 返回 404 |
| Memory 链接指向未挂载的弹窗／HTMX 目标，点击无显示 | 复用既有 evidence-modal，校验材料链接的实际目标；浏览器在隔离预览中复现无变化，修复后点击“查看材料 1”出现原图、解析和模型来源，布局核查通过 |

Codex 既有 Scope 测试原先把所有 current HTTP 操作当作 MCP 工具，新增五个图片 HTTP 操作后产生六项失败；按契约中的 HTTP-only 标记修正测试范围，并添加不存在的图片 MCP 调用不得虚构工具响应的行为检查，保持真实 MCP 白名单。另一项断连测试因本机代理将 loopback 失败变成 502；限定测试进程 NO_PROXY 后通过，未改生产传输逻辑。

### 最终检查与限制

| 检查 | 实际结果 | 范围与说明 |
| --- | --- | --- |
| Codex、图片、Dashboard、API／JS 契约综合回归 | 401 passed、6 skipped，282.34 秒 | 在以上测试问题处理后完整复跑；图片的既有模型、恢复、权限与 Source 消费回归均在内 |
| 最终 Dashboard 与图片页检查 | 25 passed，58.37 秒；补齐弹窗目标后精确历史入口再跑 1 passed，7.61 秒 | 实际 HTTP／SQLite；覆盖保存、解析、转义、跨 Scope、认证、禁用历史读取与旧 citation |
| CLI、配置 CLI、Client 回归 | 114 passed、1 skipped，22.81 秒 | 既有文字与 SDK 行为保护，不累计重复通过数 |
| 全部配置向导模块 | 172 passed、4 failed，13.00 秒 | 四项失败均在原始 HEAD 独立归档源码上复现：Windows chmod 两项、POSIX 路径引用断言、seekdb Linux／macOS 平台预期；未放宽断言或修改无关实现 |
| 48 个手写新增／修改 Python 文件 | Ruff check、format --check 与 Windows-target ty 均通过 | 包含图片基础代码、插件和新增验收测试 |
| 全库 Linux-target ty、Pydantic AI integration ty | 通过 | Windows 上的静态目标检查，不代表 Linux 运行验收 |
| HTTP／JS generator --check、uv lock --locked | 通过，260 包 | 阶段五、六不新增公开传输 DTO；契约生成一致 |
| prek run -a | 前十个 hook 通过；ty 的既有 32 条 Windows Unix API 诊断失败 | 没有把全库检查记为全绿；Make 不可用，运行对应组件命令 |
| UTF-8、metadata、本地链接、围栏与空白 | 九份文档通过；git diff --check 通过 | 内容和 JSON 元数据保留中文 |
| 用户修复、主 checkout 与私有材料 | 五个已修复边界 SHA 不变；主 HEAD 不变且仍只有原 docs/my/；凭据扫描通过 | 报告、数据库、配置与原始会话均在忽略目录；没有 commit、push、PR 或发布 |

复现真实宿主验收需设置 POWERCONTEXT_TEST_CODEX_BIN，真实 Vision 另设置 POWERCONTEXT_TEST_VISION_ENV_FILE，然后执行 `uv run --locked python -m pytest tests/e2e/test_image_codex_live.py -q`。综合回归涉及 tests/codex_plugin、test_config_wizard_images、test_dashboard、test_experimental_image_contract、test_image_vision、test_api_contract、test_js_operations 及五个图片 e2e 文件。完整输出保存在 `.powercontext/image-source-experiment/phase56/`，不写入公开凭据或模型思维链。

### 阶段状态与下一步

阶段五、六按当前边界完成，阶段七尚未执行。先按使用说明在独立实验部署启用并验证本地插件、Scope 与 Hook 信任，再准备约 50 至 100 张获授权的工作图片做配对评估；记录 OCR／关系错误、结构化成功率、P50／P95 延迟、用量、储存增量、召回收益与错误事实，并完成一致备份恢复。1B 仅在实际可用时比较，普通生成模型的实际效果也需单独验收。

Desktop／TUI／粘贴及其他 Agent 的自动捕获，需要明确追加范围后分别探测，不据 CLI 结果自动开放。现有 Windows 检查缺口作为独立维护事项保留，不影响已执行的图片验收，也不宣称完整跨平台套件通过。上游提交、发布和长期服务部署继续等待用户另行要求。

## 2026 年 10 月 9 日 Desktop 实际单图附件探测

本次目标是按用户明确授权，直接读取当前桌面聊天刚发送的附件，核实文件、消息记录、身份及现有适配器限制。只做附件探测，不扩展自动捕获范围、不启动 Server、不上传图片或调用识图模型。

- 实际 Desktop 包版本为 26.1002.7124.0。当前会话元数据声明 0.162.0-alpha.2，source=vscode，originator=Codex Desktop；该声明与查询包版本分别记录。
- 临时 PNG 为 668 × 351、38,606 字节，常规文件且不是符号链接；两次以上读取的摘要稳定。当前 user response_item 含独立 message ID 和一个 input_image；data URI 解码结果与文件字节完全一致。
- 当前 task_started／turn_context 的 turn ID 可与该用户消息关联。本轮读取窗口没有 CLI 捕获器依赖的 event_msg.user_message／local_images。只定位执行环境给出的当前会话，读取头部和最多 2 MiB 尾部，未查看其他会话内容。
- 直接调用实验 _transcript 的只读兼容检查得到 image_host_unsupported，不是实际 Hook 执行。现有已安装 powercontext 1.1.0 缓存没有实验图片脚本或其 Hook 声明；没有修改该缓存或信任设置。
- 保存被忽略的元数据报告 `.powercontext/image-source-experiment/desktop-attachment-probe/report-20261009-cbcf454d.json`，不保存图片副本、Base64、OCR、提示词或凭据。公开文档仅使用摘要前缀，不保存完整会话身份。

执行使用 `uv run --locked python -`、Pillow 文件校验、Base64 解码与 SHA-256 比较、当前会话 JSON 记录读取、实验适配器只读调用、`Get-AppxPackage` 和 `git check-ignore`。Source 接收和 Vision 请求均为零。已同步 [Codex 附件证据](codex-capture-evidence.md)；没有源码修改、配置修改、commit、push 或发布。

结论：真实 Desktop 单图附件的取得条件已验证；自动 Hook、提交时序、Stop 后文件寿命、大图、多图、图片-only、图片入库和跨会话召回仍未验收。下一步只针对已授权桌面接入做真实 Hook 时序与附件组合探测，再实施共享采集器；不把当前读取结果记成 Desktop 自动捕获完成，阶段七状态不变。

## 2026 年 10 月 9 日 Codex 多端采集扩展启动

用户已授权继续真实 Hook 时序探测，随后完成 Desktop 采集器与 CLI 共用接入，并使用 Open Code Review 审查和修复。当前新附件仍为可读的 668 × 351 PNG、38,606 字节；其发送先于新探测 Hook 安装，不能事后宣称采样了该次 UserPromptSubmit。

本次实施目标是消息级身份、有界增量读取、尽早保存可取得的附件、本地耐久待提交队列与恢复、持久客户端开关、CLI 兼容、宿主图片表示的明确来源以及 Dashboard 核查。用户提供的配置截图只用于本地字节与时序探测；真实模型联调使用不含凭据的自制样本。先准备可审阅的探测 Hook，再继续独立实现与检查；未完成的实际桌面 Hook 信任、输入或回调另记验收缺口。

全部功能修改在实验 worktree；当前桌面端内置 codex.exe 已实际查询为 0.162.0-alpha.2，与会话声明一致。现有 CLI 0.141.0 保持兼容，未知版本不自动开放。开始前记录用户既有图片实现修复的摘要并保存本轮代码快照，供最终差异审查；不提交上游。

## 2026 年 10 月 10 日 Codex 两端采集实现、联调与审查

### 授权范围与本次结论

用户授权完成真实 Hook 时序、Desktop collector 与 CLI 共用接入，并自动调用 Open Code Review 和修复。用户确认已信任临时探测 Hook；随后选择“先完成采集与识图，自动召回保留待测”。本轮据此交付耐久采集、真实 Vision、Dashboard 和可直接操作的桌面验收指南；不标记普通生成模型、实际桌面拖图／粘贴、TUI 或阶段七完成。全程只修改实验 worktree，没有 commit、push、PR 或发布。

### 实际宿主探测与实现依据

1. 实际执行桌面内置二进制 --version，确认为 0.162.0-alpha.2，包为 26.1002.7124.0。当前真实 user 图片记录使用 message ID、input_image data URI；配置截图仅本地读取元数据，没有发送到摄取或 Vision。
2. 当前聊天安装三条限定当前会话、只记录脱敏时点的临时 Hook。用户确认信任，但活动聊天未取得可靠的真实界面回调样本。不能把后来的 steering 输入算作已捕获初始 Submit。临时 Hook 已精准移除，其他配置、信任和原凭据保留。
3. 在一次性 CODEX_HOME 内运行实际 app-server。bypass 参数最初没有给予 app-server Hook 信任，回调为零；经实际 hooks/list、仅测试 home 的可信 hash 写入和 reload 后，两轮图片-only 实际得到四条回调。Submit 无图片、Stop 有图片；Hook stdin 不包含附件字节。
4. 采用独立 Desktop companion 而不依赖界面 Hook。读取支持的只读 state_5.sqlite 元数据，过滤已授权准确项目，再读增量会话；CLI 使用 Submit／Stop／Interrupt。两端共用 bytes 暂存、上传与回执。

### 代码与配置

- 新增 image_capture_config.py：准确项目／Scope／地址的持久授权，enable、disable、status、drain、retry、watch；后台进程在 Windows 隐藏启动。关闭状态优先于环境 opt-in，环境显式 opt-out 不读取当前会话。
- 新增 image_attachments.py：固定版本门控、现代消息级图片和旧 CLI 原文件、部分记录等待、48 MiB 单记录／64 MiB 单次文件读取预算。不从用户文字推断路径，不请求远程图片 URL。
- 新增 image_outbox.py，内部格式版本 2：bytes 与游标事务写入，重放保留 digest，上传成功清除 bytes；128 MiB 待提交上限、128 任务／观察记录、512 接收回执。未知旧内部格式不抹除，旧完整 JSON 回执可重放，部分旧检查点需核查或显式上传。
- 新增 image_capture_worker.py、image_desktop_watch.py：同一 OS 锁、耐久恢复、Scope／地址不转投、当前消息关联；已有聊天从启用时 EOF 开始，新聊天以父进程时间捕获首图；重新授权使旧读取游标退出，关闭前已暂存 bytes 可继续同目标投递。
- settings.py 图片设置来源与普通设置隔离；hooks.json 增加 Interrupt 与 Stop 图片命令；保留普通 prompt／召回流程。
- OpenAPI origin 新增 nullable message_id、representation、client_variant，并通过生成脚本更新 Python 模型和 schema。OriginalImageSource 只省略新添的空字段，保持旧权威指纹。
- Dashboard 来源和文案显示 host_image，保留精确原始／解析 ID 和核查链接，提示宿主可能已缩放。没有建设全量图库。
- ModelBest MiniCPM 普通 Chat 合并多个 system 消息，保留任务与输出 schema；真实请求可返回 200，但样本未生成 Memory，未将该模型作为已验证的普通生成默认。桌面配置最终去掉 Generation 和 Memory 自动计划。
- 配置向导输出增加持久开关、collector 健康确认和固定版本说明。新增 desktop-testing.md、desktop-review.md，更新指南、契约、可行性与宿主证据。

### 实测发现、修复与恢复

实际独立 collector 测试在所有 Hook 关闭时输入 localImage-only 和 data URI-only。修复了 CODEX_SQLITE_HOME、Windows verbatim cwd、冷启动首图遗漏。后续审查修复未知头／删除会话阻塞其他文件、删除项目、显式关闭优先级、重新开启补导关闭期活动消息、同身份换字节被忽略、可选设置损坏普通召回，以及 SQLite 连接没有关闭。查询三种路径表示采用同一固定 IN 查询，项目总上限 32，单次读取严格剩余预算。

单独准备仅 Vision 的桌面运行配置时，原测试 DB 的 Memory 维护 manifest 拒绝配置变动，按契约另建独立 capture-only DB；普通计划别名 memory_schedule_seconds 也明确移除，未放宽运行时校验。

新资产临时路径达到 272 字符，Windows 写入返回 FileNotFoundError／HTTP 500。尝试变更资产目录被数据库根目录绑定拒绝，未绕过或改写绑定。最终在 ImageService._path 中为 Windows 文件夹验证与 I/O 使用扩展路径，保持原根目录和身份。客户端原两张暂存以相同 Scope、键和 bytes 恢复上传，真实 35B 两任务 succeeded，保存与回读摘要一致，OCR 包含 E429、64、30，Dashboard HTTP 200。专门补充原生 HTTP 长路径接收／重放／停服／关闭后回读测试。

随后重新运行最终正式 collector 的实际 app-server，两轮各完成，两个耐久接收回执，所有 Hook 关闭，退出码 0。实际 Vision 均 succeeded；客户端 input_digest、Original 资产摘要和下载摘要完全一致。页面 OCR、Desktop／host_image 来源和任务页面 200 已核查。实测摘要为 desktop-integration/final-desktop-readback.json，失败恢复摘要为 capture-only-readback.json。

用户此前 tasks.py、vision.py、worker.py、server/images.py 的摘要与开始快照一致。service.py 仅添加上述 Windows 路径处理，保留用户已修复的耐久发布、维护、恢复及路径校验。

### 本机可用环境

已通过内置 Codex 原生 plugin marketplace add／plugin add 安装 powercontext@powercontext-local，来源为实验 integrations/codex；安装缓存包含最终代码，依赖已准备。仅调整此新缓存 .mcp.json 到 127.0.0.1:18171，使用既有 URL 绑定鉴权读取机制，没有覆盖原 credentials 或 minicpm.env。

暂时停用原 powercontext@powercontext，避免同名插件和 MCP 冲突；其他插件未修改。配置备份在私有忽略目录 codex-before-desktop-install.toml，不盲目用整份备份回滚后续用户修改。实验图片持久授权只对 worktree 生效；主 checkout 里的当前敏感附件不会自动上传。新的正式插件仍需要 Codex 用户审阅信任，临时探测信任不能移作该信任。

Server 保持运行于 127.0.0.1:18171，仅绑定 loopback，实验访问控制关闭，Dashboard／MCP／images 开启；独立 SQLite、assets 和环境文件在 desktop-demo/capture-only 与 desktop-demo.env。已新建验收 Scope，启动信息在 desktop-setup.json。正式全局 companion 心跳持续更新、running=true、error=null；只读项目索引没有回读当前主聊天。真实 GUI 仍须用户新建属于实验目录的聊天，见桌面测试指南。

### Open Code Review 与修复覆盖

实际执行 ocr review --audience agent --background-file <本次背景> --exclude <本次排除清单> --format json --concurrency 3 --timeout 5 --max-tokens-budget 100000。OCR v1.12.13／deepseek-flash 规划首组约 294,736 tokens，超过预算；结果 failed，完成文件 0，17／17 选择项失败。没有把 0 finding 记成通过，也没有盲目反复调用。

按插件 Delegate Skill 执行 ocr delegate preview 与 delegate rule，主持审查完成全部 17 个选择项，跳过 0，覆盖 100%；另取 service.py、config_wizard.py、pyproject.toml 的补充规则，并检查测试和文档。逐文件清单和已修复问题在 delegate-review-result.json，公开结论见 desktop-review.md。当前没有遗留已确认阻塞问题；实际界面、TUI、普通生成／召回与规模评估保留为验收待办。

### 执行的验证

| 检查 | 实际结果与边界 |
| --- | --- |
| uv sync --locked --extra images --extra server --extra cli | 通过，独立环境；Windows 运行中的入口锁需先停服务处理，未改锁文件 |
| 主要 Codex 插件／契约／图片／Dashboard／Vision／推理检查集 | 413 passed、6 skipped，238.09 秒；真实 CLI 为显式 opt-in，普通集不冒充宿主验收 |
| 后续图片接收、恢复、Dashboard、配置、采集检查 | 82 passed，136.41 秒；包含 Win32 长路径回归 |
| 最终采集器与 Win32 专项 | 24 passed，12.52 秒；与主要集部分重叠，不相加为总数 |
| 剩余读取预算修正后的完整采集专项 | 35 passed，6.84 秒；固定版本、暂存、回执、开关与两端读取检查 |
| POWERCONTEXT_TEST_CODEX_BIN=<桌面内置 0.162.0-alpha.2> 的 test_image_codex_live.py | 1 passed，50.07 秒；实际 CLI／Hook、PNG＋JPEG与重启引用，Agent／Memory 受控 |
| 同上切换 npm 0.141.0 二进制 | 1 passed，58.01 秒；原文件与旧键兼容 |
| 最终 app-server --api 实测 | 两轮 completed、两个 collector accepted 回执、退出码 0，Hook 全关；真实 Vision／字节／页面核查通过 |
| ruff check、format --check、受影响文件 ty | 通过；不宣称整个 Windows make check 无历史缺口 |
| generate_api.py --check、generate_js_operations.py --check | 通过，没有手改生成文件 |
| git diff --check | 通过，仅已存在换行转换提示 |
| 当前主机只读索引 IN 查询，EXPLAIN 与 10 次执行 | 使用 archived/cwd 索引，最多 32 行，平均约 0.49 ms；仅当前数据集 |

原始文件和结果保存在忽略的 desktop-integration 目录。没有将真实图片、完整当前用户会话身份、普通／Vision 凭据或 Base64 写入公开文档。公开 fixture 为自制 SPRUCE-27 故障说明，PNG／JPEG 单独识图，不承诺多图联合理解。

### 待完成与验收顺序

1. 当前工作结束后，新建实验目录的真实 Desktop 聊天，加载本地插件、审阅 Hook，先按 fixture 拖图验证 accepted／succeeded／Dashboard，再测粘贴、图片-only、多图、同图新消息、断网恢复和关闭重开。
2. 按用户选择，普通 Generation 模型、自动 Memory 和同 Scope 新桌面聊天的真实命中／citation 留待后续，不将 Source 页面当作自动召回证明。
3. 交互 CLI TUI／未知版本和其他 Agent 需各自真实宿主验证；当前仅声明两版 exec 与固定 Desktop 内核。
4. 阶段七继续工作样本质量、延迟／用量／空间、配对收益、长期运行和一致备份恢复；1B 只有实际可访问时比较。
5. 上游提交、发布和长期部署仍未授权；结束本机实验时使用持久 disable，切回原插件，停止本地服务，保留可恢复的配对数据。

## 2026-10-10：真实桌面发送核验、Dashboard 入口说明与新内核兼容

### 范围与实际证据

按用户请求排查“Hook 成功，但 Dashboard 没有数据”，继续只验证采集与识图，普通生成模型与自动召回仍保留待测。本轮核验用户已发送的真实桌面图片、客户端回执、服务任务、资产下载及实际浏览器页面；没有代操作 Codex 界面发送新附件。

三张用户桌面 PNG 均为 accepted／succeeded，已保存原图 Source 与解析 Source。安装 App 索引中的会话及规范化 cwd 与明确授权的 worktree 一致，来源为 desktop／host_image。真实 MiniCPM-V-4.7-35B-A3B 返回 HTTP 200，三次延迟为 2763、3971、2731 ms；OCR 均包含验收图的 SPRUCE-27、64 MB、E429 和 30 秒重试。下载资产与保存 SHA-256 一致，未输出图片 Base64 或凭据。完整只读核验在忽略目录的 2026-10-10-desktop-readback.json。

通过 Browser Use 在 Codex 内置浏览器打开同一 Scope 的首页与图片任务页。首页只展示 Memory 等 Artifact，当前没有全量 Source 列表；图片任务页能直接显示原图和识别结果。原有 evidence 路由已经能从 Memory 引用查看 Source，新 image-task 路由为未生成 Memory 的图片提供独立入口。可读解析已接入既有提炼流程，当前普通 Generation 和 Memory 自动处理未开启，且没有图片专用人工确认按钮。没有将首页空白判为摄取失败，也没有将 Source 解析成功判为自动召回成功。

### 运行恢复与兼容修复

检查开始时，用户启动的实验 Server 在 18171 正常监听，但独立 Desktop companion 的心跳已停止。使用持久配置的 enable 恢复采集；不切换 Scope，不修改授权 epoch，不回读关闭期间内容，也不停止用户手动启动的 Server。

同时发现安装包已更新到 26.1007.2314.0，实际二进制为 0.162.0-alpha.17.2。已有成功用户会话头来自 alpha.2，不能用旧记录证明新内核可用。对新二进制执行全新隔离 app-server 的 localImage-only 和 data URI-only 两轮：Agent 使用 loopback 受控 provider，Hook 全部关闭。旧采集器明确返回 image_host_unsupported，零接收回执，两轮宿主本身均 completed。

在 image_attachments.py 仅新增 alpha.17.2 的两条明确宿主组合：vscode／Codex Desktop 与 exec／codex_exec。未知版本策略保持拒绝。新隔离 home 重跑实际 app-server：两张正式 collector 回执 accepted，真实面壁识图均 succeeded，保存字节等于宿主消息中的图片，OCR 事实与摘要通过回读。材料在 runtime-alpha17-baseline 与 runtime-alpha17-verified/readback.json，均被忽略。该测试证明真实内核和采集链路，不宣称自动化操作了桌面拖图、粘贴或多附件界面。

已将相同附件读取代码同步到本机 powercontext-local 安装缓存，保留修改前副本于忽略目录。核对正在运行的脚本路径后重启正式 companion，未改 Hook 命令、用户信任配置、Server 配置或 Key。Scope 与 epoch 保持原值，已有回执仍可读。

### 页面与说明修正

实际浏览器发现独立原始材料页持续显示加载转圈：该页没有 HTMX 请求，也不加载自动隐藏 indicator 的 HTMX 脚本。evidence.html 现在只在嵌入式异步查看中渲染 loading indicator，独立页面不再显示已结束的加载状态。重新加载实际页面后核验指示器消失，原图与 OCR 保持可见；原有异步 Memory 证据查看仍保留指示器。

桌面指南补充“首页空白时先看图片回执”的直接操作、打开已接收图片的 PowerShell 命令、Source／Memory 的生成与展示区别、已信任插件的启用方式、Server 与 companion 的不同生命周期、心跳判断的短时限制，以及当前明确支持的内核版本。没有扩展为图库、人工确认流程或普通模型部署。

### 验证与增量审查

| 检查 | 实际结果 |
| --- | --- |
| Codex 图片采集／投递／Desktop watcher 与 Dashboard 专项 | 41 passed，23.75 秒，包含新旧明确版本的投递与重放行为 |
| 新内核实际 exec／Hook、PNG／JPEG、重启引用测试 | 1 passed，77.73 秒；Vision 和普通 Memory 生成器为受控组件 |
| 新内核实际 app-server，无 Hook，正式 collector 与真实 35B | 两轮 completed，两个 accepted／succeeded，宿主字节与回读一致 |
| 用户三张真实桌面图回读 | 任务、Source、模型、OCR、资产摘要与项目范围核验通过 |
| 实际浏览器页面 | 原图、OCR、模型来源可读，独立页持续加载指示器已消失 |
| Ruff check／format --check | 本轮改变的 Python 文件通过 |

按 REVIEW.md 对本轮增量检查：版本准入仍以精确宿主组合为边界，未放宽未知格式；新的处理继续使用已有身份、Scope、暂存和去重规则。页面修改只改变独立视图的加载提示，不改变数据读取或鉴权。没有新增公共 HTTP 契约，也没有手改生成文件。后续需要分别验收界面输入组合、采集器长期运行、真实普通模型的自动 Memory 与新会话 citation；图片列表与人工确认属于额外交互规划。

## 2026-10-10：启用真实普通模型与图片 Memory 处理

本轮目标：按用户明确提供的主项目 `.env` 路径，只读取普通 Generation 与 Embedding 配置，接入隔离实验服务；保留原 Scope、数据库、资产、Source 身份及 Vision 配置。开启 Memory 后处理已有图片解析 Source，核验真实模型产物、检索与 Dashboard 的“原始材料”入口。主项目配置和服务不变，不向上游提交。

### 配置、维护与实际执行

- 现有 MiniCPM-V 35B 作为普通模型的只读预检请求成功但返回零候选，未写 Memory 或推进游标。按用户随后提供的路径，只复制普通 generation 与 embedding inference 项，不复制主项目数据库、运行策略、Scope 或数据。
- 实际配置为 `openai-chat:deepseek-flash`／DeepSeek endpoint、`openai:BAAI/bge-m3`／SiliconFlow endpoint；1024 维 unit profile。两次真实预检分别得到两条候选与有效向量。凭据保存在忽略目录，无公开输出。
- 使用 SQLite online backup 创建开启前一致快照，integrity_check=ok，另保留实验 env 副本。资产仍绑定原目录，未删除或重新解析。此快照用于本轮回退与比较，不替代完整数据库／资产配对恢复验收。
- 设置 `artifact_processing_families=["memory"]` 和 `memory_schedule_seconds=30`。核对 18171 所属命令后，仅停止原实验服务。首次重启被已有 maintenance manifest 拒绝，使用 `server processing-migrate --action plan` 检查，required=true；确认旧 Server／Worker 已停、无法继续写入后，以新 migration-id 和 `--maintenance-confirmed` 执行 apply，返回 ready=true、issues=[]。没有绕过配置一致性检查或另建数据库。
- 用相同 `desktop-demo.env`、端口、SQLite、资产及 Scope 隐藏启动服务。健康检查为 ready，generation／embedding 可用，memory worker leader；更新本地 setup 的实际端口 owner PID 和 generation_deferred=false。正式 collector 同 Scope 继续运行，error=null，三张用户附件的 accepted 回执仍在。

### 结果与证据级别

| 验证 | 实际结果 |
| --- | --- |
| 真实 DeepSeek 与 BGE-M3 只读预检 | 两条 Memory 候选、一个有效 1024 维向量；此步骤未持久化 Memory |
| 正式后台 Memory 处理 | 在显式 flush 前已自动生成两个 active 条目，Memory revision 1；未手写条目或注入受控生成器 |
| 显式 `/v1/memory/flush` | idle；previous_cursor=current_cursor=high_watermark=20，processed_source_count=0，表示后台已消费完积压 |
| 原有 Source 与数据库完整性 | 原 20 条身份、payload、journal_position 与开启前快照一致；9 原图＋9 解析＋2 普通内容，integrity_check=ok |
| FTS／vector／hybrid 检索 | 三种实际模式各命中两条 Memory，向量使用真实 BGE-M3 |
| `/v1/context/prepare` | ready，包含 E429 等待 30 秒与 64 MB 上限及具体 Memory citation |
| Memory 引用的五份图片解析 | 通过正式任务接口回读，Scope 与精确分析 Source 身份匹配，均为 desktop 来源 |
| 实际 Dashboard 点击 | 记忆列表展开“原始材料”并点材料，历史原图、OCR、模型及宿主来源可读；页面截图已保存 |
| 主项目与隔离边界 | 主 `.env` SHA-256 不变；只使用实验数据库、资产、端口与 Scope，未提交上游 |
| 最终配置与文档检查 | `config validate`、`git diff --check`、UTF-8 与文档本地链接校验通过；凭据副本及数据库快照均被 Git 忽略 |
| 安装插件的实际目录 Scope | 从实验 cwd 执行已安装 `scripts/scope_binding.py`，解析为既有验收 Scope；MCP endpoint 仍为实验 18171 |

完整预检、online backup、维护 checkpoint、服务日志、processing-result.json 和浏览器截图位于忽略目录 `desktop-integration/memory-enable/`。文档已更新真实配置、处理计划、维护前提、Memory 材料入口和新桌面聊天验收步骤。本轮没有修改运行时代码、公共 API 或 Source schema；验证使用真实模型、正式 HTTP 与浏览器，不新增无必要的实现镜像测试。

### 限制与下一步

两条结果来自已有自制验收图，模型保留 OCR 来源限制并合并重复事实，不承诺一图一记忆。后台检索与上下文返回不能代替全新桌面聊天的实际注入或工具调用。下一步在同一实验目录与 Scope 新建聊天，不附图，先明确要求 PowerContext 搜索并核对 citation，再另测普通问句的 Hook 自动召回；界面输入组合、长期运行、阶段七工作样本评估和完整配对备份恢复继续待验收。

## 2026-10-11：标准安装、模型复用、采集生命周期与 Dashboard 整合

本轮目标：执行用户批准的正式使用流程建设计划，在原实验 worktree 对齐当前 master，保留已有开发成果与真实数据；统一普通向导和环境配置、复用兼容的多模态 Generation、正式 Codex setup 与客户端管理、Scope 联动、Dashboard 图片材料及诊断。完成相关行为测试、真实模型与宿主验证，使用 Open Code Review 审查并修复，再交付复杂图片及手动验收案例。不修改主项目配置，不提交上游。

工作前保存非忽略文件 Git 快照及数据库／资产配对备份。Git 备份为 ce9f0b1d257c7b0a179a1bf72f72313199dc3104，保留可恢复 stash；worktree fast-forward 到 master 6b7195167d0bba68ba4c46c843767af43ec42772，再恢复原实验改动。五处冲突结合主干和图片行为解决，没有丢弃现有 Memory、Topic、证据或 Dashboard 改动。只停止经过身份核对的实验进程，没有启动或修改主 checkout 的服务。

### 最终实现

- 标准 config init 提供可选图片配置，保留兼容开关；单份 .env 管理 Server、模型和 Codex setup 的授权选择，常规 server extra 包含 Pillow，SDK 默认依赖保持轻量。
- 增加明确 generation 复用选择；使用 generation 模型、endpoint、显式 headers 或现有 OpenAI Chat 使用的 OPENAI_API_KEY，显式 Authorization 优先。独立 Vision 不借用普通模型凭据。部署标识按所选连接派生，已有任务按固定配置校验，历史 Source 不重新识别。
- 标准 setup codex 保存项目授权、校验图片服务就绪，并将选定 Scope 写为服务拥有的 workspace binding；后续采集读取实时会话／项目绑定，不用授权文件静态 Scope 代替。暂存任务保留原目标与身份。
- 增加客户端启动管理及单一监督进程。Windows 用户任务计划创建实际被权限拒绝，采用无需管理员权限的用户 Startup 项；注册／移除和子进程恢复均实际验证。重复 setup 自动停掉由自身启动项确认的旧 Python 进程和后代，重新加载新采集代码，保留耐久 outbox。其他平台采用 launchd／systemd 代码，尚未原生验收。
- SessionStart 恢复客户端，Interrupt 遵守三秒外层上限和 1.5 秒内部预算；新 Hook 定义仍由用户在 Codex 信任。关闭最后一个项目移除自己的启动项，禁用插件使监督进程退出，不导入授权前或关闭期间历史。
- 新增受权限和 Scope 约束的图片 list／status HTTP、Python SDK 与 CLI；以 OpenAPI 生成 Python 和 JS 表，未手改生成文件。新增 task.created_at 及分页索引，旧日期保持未知。省略原图目录保留旧库记录的绝对目录，明确改目录仍拒绝。
- Dashboard 新增图片材料列表、分页、状态和缩略图；缩略图并发有界且不缓存私有原图。保留原图／解析详情及 Memory 精确历史材料链接。
- doctor codex 检查授权、客户端注册和运行、Scope、图片服务与模型配置；原生诊断忽略明确禁用的旧插件 cache。image list／status 普通模式显示结果。已知可恢复 SQLite 故障退避恢复，未知不变量停止新接收并报告，历史回读可用。
- 根 .env 已从已有演示环境归并，沿用原端口、SQLite、资产目录、Scope、独立 35B、DeepSeek 和 BGE-M3；未覆盖当前 Memory family 和 30 秒周期。更新双语正式文档、桌面指南和复杂图片案例。

### 测试与真实执行

| 检查 | 实际结果 |
| --- | --- |
| uv lock；uv sync --locked --extra server --extra cli | 成功；未显式加 images extra，常规安装已有解码器 |
| 图片／Codex／契约／Dashboard／Topic／统计回归 | 527 通过、6 跳过；两个旧 Dashboard 向导输入缺少新增可选回答，修复后与 CLI 输出用例复测 4 项通过。最终相关跨组件 529 项均通过；跳过条件保留 |
| 配置、CLI、安装与原生 transport 回归 | 224 通过、1 跳过 |
| 最后兼容、Dashboard 安装与 CLI 回归 | 11 通过 |
| 生命周期、向导、损坏配置、CLI 补查 | 19 通过 |
| 模型复用及 Vision 最后回归 | 23 通过，包括环境 key、显式 header 优先及独立角色隔离 |
| 生成 API／JS --check 与契约测试 | 通过 |
| Ruff check、format 与 Git diff --check | 通过；prek 的格式、JSON、YAML、TOML 和冲突检查全部通过 |
| 类型检查 | 受影响文件与 Linux 目标全库通过；Windows 全库 ty 仍有既有 POSIX 原生代码扫描属性错误，主 checkout 也复现，未修改这些无关模块 |
| 源码 wheel 与文档 | uv build --wheel 成功；20 份变更 Markdown 的 UTF-8 和本地链接检查通过 |
| 标准 Server run --env-file .env、setup codex、doctor codex | 实际运行成功；最终 doctor 全部 OK，图片 pending=0、worker 运行、启动项注册、采集器 error=null |
| Windows 原生 Startup 生命周期 | 隔离 home 实际注册及移除均成功；真实采集器 PID 40068 终止后自动恢复为 58048；最终重复 setup 清理旧 60556／58048 并启动新进程，未手工清理文件或 outbox |
| 真实 CLI 0.141.0 exec | 1 项跨组件测试通过：单 PNG、PNG＋JPEG、原图字节、服务重启、准确来源和新会话召回；Agent／Vision／Memory provider 受控 |
| Desktop 附带 alpha.17.2 独立用户 Hook 测试 | 没有取得回执；记录为未通过，不解释为原生插件路径已失败或已通过，不放宽门禁 |
| 真实复用模型连接探测 | 临时将已有 35B 连接作为 generation 复用；一请求、schema_valid=true、recognized_probe_text=true、Source 未保存；没有改实际普通生成配置 |
| 复杂图识图 | 合成图 1536×1024，1,495,423 字节；正式 HTTP 显式上传，真实 MiniCPM-V 35B succeeded；下载字节一致 |
| 复杂图自动 Memory | 后台真实 DeepSeek 自动生成八条，未显式 flush、手写记忆或注入候选；规则／废弃／未确认信息保留 |
| 后端检索与上下文 | FTS 命中五条，真实 BGE-M3 vector／hybrid 各八条；context prepare 为 ready |
| 浏览器实际使用 | 图片列表及缩略图正常，打开原图／OCR；展开 E731 Memory 并点原始材料，回到同一分析 ID 和同一图片 |
| 旧数据保护 | 48 条旧 Source 的身份、payload、journal_position 全部不变；四个旧资产摘要一致；integrity_check=ok。新增验收材料另存 |

这些测试集合存在交集，不相加宣称独立总用例数。API 受控输入、CLI 实进程、真实远端模型、浏览器及真实 GUI 上传分别记录。复杂测试图片不等于本轮桌面拖图；原有用户真实桌面附件与回执保留，没有把旧图当新图。

### Open Code Review 与交付

实际外部审查返回 partial，58 个条目中 53 个未完成，产生五条意见。已核实并修复部署标签、损坏配置、可恢复数据库故障和重复检查；资产预留意见按原有耐久预算及孤儿宽限契约核对，不立即删除可能被并发上传使用的资产。用同一插件的 delegate preview／rule 对 59 个选定文件的本轮增量补审，修复 endpoint 诊断、历史目录、CLI 输出、Scope 及启动更新等问题；八个与保存 checkpoint 一致的文件没有新整合增量。没有宣称外部全量审查通过。

记录、覆盖清单、真实模型结果、原生生命周期和完整性报告保存在忽略目录 .powercontext/image-source-experiment/integration-*。公开结论见 [整合审查报告](integration-review.md)，最终测试方法见 [桌面指南](desktop-testing.md)，新素材见 [复杂测试案例](complex-test-case.md)。主干同步快照仍保留，无提交、推送、PR 或发布。

### 继续验收

重启 Codex，信任更改的 Hooks，在原授权 worktree 拖图并检查新任务、Memory 原始材料和全新聊天的实际答案及 citation。分别补验粘贴、JPEG、多图、中断、关闭／重开、CLI 新内核原生插件加载、其他平台、长期运行、更多真实工作样本及完整配对备份恢复。实验性边界保留，用户确认后再推进主分支合并。


## 上游 #1932 PR 准备：修复、rebase 与真实验收

本轮目标：修复复审发现的 Hook 集成检查、授权期间采集器恢复漏图、观察记录预算阻断已暂存投递及测试格式问题；保存代码与旧 SQLite／资产配对快照；将实验分支 rebase 到最新 upstream/master，并按 Atomic Memory 的公共契约适配 Source 消费、材料引用与 Dashboard。完成受影响行为测试、真实 Codex／模型／浏览器验收和 Open Code Review 复审，修复发现的缺陷，整理针对 #1932 的 PR 材料。

边界：图片默认关闭、原图与历史解析身份保持可读、现有 Scope 授权和预算不放宽；本地模型自动下载／托管仍是后续提案。只修改实验 worktree，保留主 checkout 的配置与数据。用户已授权本地修复和 rebase；本轮准备可审核结果，不自动发布或推送。真实 GUI、CLI、HTTP、模型及隔离故障测试分别记录证据。

### 2026-10-12：主干适配与中断时的实际结果

- 保存代码恢复分支和 SQLite/资产配对备份，将实验分支 rebase 到 upstream/master 95bfbc9e；解决 12 个冲突后重新生成 OpenAPI 的 Python/JS 派生代码。主 checkout 未改动。
- 修复 Interrupt 清单、授权期间恢复漏图、观察预算阻断已暂存投递；改用 Atomic Memory 的 Source-only 抽取、独立 Artifact 来源、EvidenceChild 和当前 Dashboard 阅读机制。
- 使用官方 Atomic Memory 迁移将旧库 31 个 entry 导入，readiness 为 ready。核查原有 70 条 Source 和五个资产保持一致；尚未进行配对恢复验收。
- 292 项受影响回归、51 项 provider/向导、29 项 watcher/生命周期/诊断、14 项协调/生成测试通过；集合存在重叠。真实 CLI 0.141.0 的受控模型跨组件测试通过。更广测试 231 通过、3 跳过、2 失败，失败在同版本 upstream 基线上复现。Ruff、Linux 目标 ty、生成契约、锁文件和 wheel 构建通过，Windows 全库 ty 的既有 POSIX 错误未解决。
- 真实 Desktop 在新 Scope 通过文件选择器提交复杂合成图，自动采集后真实 MiniCPM-V 35B 解析成功。Dashboard 可查看缩略图、OCR 和原图，下载摘要及字节一致。此步骤不证明拖入、粘贴或全新聊天召回。
- Flash 普通模型输出未通过完整 Atomic Memory 协调，使用同账号的 Pro 做独立协议探测，仍出现创建动作正文为空。补齐协议说明，并在结构化生成中拒绝空写、利用已有请求上限重试，增加对应回归。真实后台最终发布和新会话召回保留待验收，不注入 Memory 或候选。
- 实际 Open Code Review 外部审查超时：24 项完成、9 项失败，其余未完成；已修复停止超时和异常配置问题。delegate 覆盖清单已建立，最终完整补审仍未完成。
- 继续桌面操作时，用户以 Esc 停止 Computer Use。立即停止 UI 操作并说明未达到 PR 就绪；未把中断后的后台行为或未完成测试计为通过。

### 2026-10-12：提交 #1932 草稿 PR

用户明确要求先向上游提交对应 issue 的草稿 PR，本轮目标改为保存当前修复和准确状态、推送实验分支并创建 Draft。此前禁止上游推送的范围已被这次明确授权解除；不进行合并、发布或标记 ready。

核实 #1932 为 open 且没有维护者评论。草稿包含当前图片 Source 与 Codex 采集实现、主干适配和已验证修复；managed-local 未实现，真实 Atomic Memory 发布/新会话召回、配对恢复、最终复审和完整检查均列为待完成。公开报告更新为当前 Atomic Memory 基线，避免旧实验成功结果被误读为本轮完整验收。提交前检查格式、受影响回归、契约、锁文件和新增文件中的密钥风险；配置、数据库、私有图片和本地探测材料继续被忽略。

提交前最后联合回归 43 项通过；Linux 目标 ty、生成 API/JS 校验、锁文件和非类型 prek 检查通过。原生 Windows ty 仍为 32 个既有 POSIX 诊断，因此本次提交仅跳过该 hook，已独立执行 Linux 目标类型检查；未修改仓库检查配置。新增文本的高置信密钥模式扫描没有匹配。

实际推送 origin 的 codex/experiment-image-source，并通过 gh pr create --draft 创建上游 [草稿 PR #1956](https://github.com/oceanbase/powercontext/pull/1956)，目标为 master、关联 #1932。回读确认 OPEN、isDraft=true，已附加到当前聊天。当前修复已提交，私有配置和运行材料未上传。下一步继续当前 Atomic Memory/新会话召回、配对恢复和最终完整复审，接受维护者对范围和 RFC 的反馈后再转为正式评审。

## 2026-10-13：草稿 PR #1956 的 CI 修复与冲突解决

本轮目标：读取上游失败任务的实际日志，区分同一根因导致的矩阵失败与独立缺陷；保存分支快照并对齐最新 upstream/master，解决集成冲突；修复对应代码、许可证和文档/生成契约问题，运行相关检查后更新 PR 分支，确认新提交的 CI 结果。用户已明确授权修复并更新该草稿 PR，不进行合并、发布或转为 ready。

前置状态：实验 worktree 干净，PR head 为 df4c7666；检查显示冲突。失败包含四个 Python 版本、Desktop、License、E2E harness、DSH 和网站。最新主干新增宿主 guidance、连接取消清理及 Desktop 版本检查等机制，需保留这些行为并适配图片路径。普通模型真实召回等既有待验收事项不因 CI 修复而自动完成。

### 修复与本地验证

- 保留 codex/backup-pr1956-before-ci-20261013 快照，并 rebase 到 upstream/master b5bcfe24。唯一内容冲突位于 hosts.install_host：保留新的 HOST_GUIDANCE installer 调度与每个宿主选项，只对 Codex 传递显式 capture_images；增加启用、关闭及未指定三种取值的边界回归。
- 四个 Python 版本的原始失败来自相同的两条向导用例：最后保存的 y 被新图片步骤误读为启用。输入增加明确关闭回答，保留远程地址/Scope/下一步断言，并增加默认关闭的配置断言。两条原失败本地通过；相关向导、安装、guidance 与能力清单 170 项通过。
- 根据真实 License 日志，为 16 个 Python/HTML 文件换用仓库完整 Apache-2.0 头；不修改许可检查或排除规则。
- 运行 Desktop 官方 generate，同步 api.d.ts、operations.ts 与 Rust operations.json。lint、generate:check、typecheck、28 条 UI 测试与前端 build 通过，不手工编辑派生类型。
- 重建并规范化 DSH lib/index.js，包含新增七个操作。318 条单元测试通过、1 跳过，9 条跨组件测试通过。
- 使用 uv lock --project e2e/bub 更新其对根包新增 images/Pillow metadata 的快照，无其他包版本升级；根、harness、Codex runtime、evaluation 与 Dify 的 lock check 均通过。
- 将三个指向个人插件缓存的 Markdown 链接改为已核实存在的上游 Skill 源码链接。没有关闭网站链接验证。本机网站原生依赖下载遇到 curl 23，完整网站构建由新提交的上游 CI 继续验证；harness 大型依赖仍在下载，不把本地未完成测试记作通过。
- 图片、证据、API/JS 契约、Vision 与生命周期联合回归 99 项通过。Ruff、格式、Linux 目标 ty、release-version、129 个 action pin、生成 API/JS、集成 guidance/能力文档检查和非类型 prek 全部通过。原生 Windows ty 的既有 POSIX 问题保留，不修改检查配置。

推送使用精确 force-with-lease，要求远端仍为已读取的 df4c7666，避免覆盖别人的更新。后续以更新后提交的 CI 和 mergeable 状态确认结果；完整真实召回、配对恢复及最终 OCR 验收仍是独立事项。

### 更新后的 CI 继续修复

078cf6f8 已推送，GitHub 确认 MERGEABLE，原内容冲突消失。新的 License 与第一批检查通过，但 harness 走到测试后发现旧 fixture 只复制三个插件文件，缺少新增配置模块：改为复制实际插件目录并忽略缓存，继续断言 harness URL 正确且图片默认关闭。未弱化生产配置或吞掉缺失模块错误。

新主干的 ZCode acceptance 还暴露旧 Skill pin 与 #1877 生成产物不一致。使用官方 pin --revision 命令重新绑定 b5bcfe24 的不可变 Git 字节，更新说明；严格摘要校验保留，不把重置 pin 当成真实模型资格。分别继续执行离线 pin/fixture 测试和后续 CI，真实 ZCode 模型行为需另行验收。

官方 pin check 通过，ZCode 离线与协议/宿主测试 69 项通过；Linux 目标 ty、Ruff 和差异检查通过。两个残留 CI 修复单独提交后更新同一草稿，继续检查最新提交的全部任务，保留 Draft 状态。

### Memory 写入并发审计的 CI 回归

1ef1bd94 的严格配置和 harness 检查通过，ZCode 实际 CLI 验收首次出现一个原生进程异常退出，另一组独立验收通过。单独重跑后，一组通过，另一组在更新 Memory 时收到 access_unavailable 503。保留两次实际日志与验收摘要，不以重跑或降低断言代替修复。

隔离 SQLite 服务复现了准备阶段权限审计的 database is locked：AtomicMemoryManagementWriter.prepare_command 在读取事务内追加决策审计；并发提交使该快照不能升级为写事务。复用现有 deferred_decision_audit，使决策仍在原准备事务中检查，审计在该事务关闭后落库；正式提交仍使用原有权限、版本和 Owner 校验。

新增跨组件回归使用实际 SQLite 独立提交插入并发点，通过公开 HTTP 验证创建、更新、最终正文与 revision、审计可读和旧 ETag 拒绝。两例在去掉修复时均以 access_unavailable 503 失败，恢复修复后两例通过。进一步执行 Access/Atomic Memory 联合回归，再更新 PR 并核查最新提交的完整 CI。

Access/Atomic Memory 与 ZCode 协议、宿主联合回归 85 项通过。完整 Python 3.11 CI 的 4669 项单元测试通过后，E2E 又发现 Dream 向导新建用例缺少图片关闭回答，以及已有普通模型的字符串 URL 被 MiniCPM 专用 host 检查读取。补齐该用例的明确关闭输入；适配条件先检查 MiniCPM 模型，再按实际请求使用的字符串 URL 解析 hostname，不改变其他 provider。已有 Dream admission 和真实 MiniCPM 单 system message 回归继续执行。

两个 Dream 向导用例及 MiniCPM 请求格式回归合计 3 项通过；后者使用受控 HTTP provider，不记作真实面壁请求。Ruff、Linux 目标全库 ty 和差异检查通过。更新后的修复保持审计必需、图片默认关闭和原有 admission 断言，继续由新 head 的完整 CI 验证。

## 2026-10-13：PR #1956 适配最新召回权重契约

本轮属于主干集成维护：用户要求解决同一草稿 PR 的新冲突。前置状态为干净 worktree、分支与远端同为 90902943；该提交的 CI 已完成，32 项成功、1 项按 workflow 条件跳过。最新 upstream/master 为 28244c8a，包含召回通道权重配置和依赖 action 更新；预检发现两份 Desktop 派生操作清单冲突。

目标是在保留分支快照后 rebase 到当前主干，从合并后的 OpenAPI 契约重新生成各端派生文件，验证图片与召回配置、类型及契约检查，再以精确 lease 更新现有草稿并确认可合并状态。保留默认关闭、Scope/授权边界、历史原图/解析 Source 身份和已有 CI 修复；本轮不扩展功能、不操作现有桌面服务，也不把受控测试当作真实图片或新会话召回验收。

### 契约冲突与验证

- 建立 codex/backup-pr1956-before-recall-rebase-20261013，rebase 到 28244c8a；仅 Desktop operations.ts 和 Rust operations.json 发生内容冲突。运行 pnpm --dir desktop generate 从合并后的契约重建，range-diff 确认原有七个功能/修复提交保留，变动仅为生成的契约摘要。
- 本机没有 make，执行对应 recipe：uv run --locked python scripts/generate_api.py、JS 生成/检查及 API 检查；Python 生成结果无漂移，Dify 契约检查通过。保留主干 score 约束、两类 recall_channel_weights 注入、Memory 延迟审计和 MiniCPM 单 system message 适配。
- 以 Python 3.14 顺序运行 API、图片解析/恢复/证据/Dashboard、召回权重/fusion 及 Access 并发联合回归，402 项通过。Desktop lint、生成/图标/格式检查、28 条 UI 测试和前端 build 通过。
- Ruff、1260 文件格式检查、Linux 目标全库 ty、根锁文件、129 个 action pin、生成 guidance 和 ZCode pin 检查通过；这些是离线或受控 provider 验证，未重新操作真实 Desktop 或 MiniCPM 服务。

推送前 upstream/master 又推进至 75481152，新增 pytest-xdist、拆分 unit/e2e CI 矩阵和相应锁文件，预检没有新增代码冲突。继续同步该提交并验证图片/宿主测试在并行执行下保持隔离，更新文档基线及同一 PR；不回退上游并行检查。

两 worker 首次回归 116 项通过、1 项失败。检查失败测试的隔离数据库：被终止前的第 1 次调用已在约 1.5 秒后正常超时，记录为 failed，随后第 2 次调用启动；不是租约提前恢复或生产重试错误。真实进程终止用例把调用超时设为 1 秒，负载下可能在测试控制器得到 provider entered 信号前先超时，且重启后的第一次查询时机不是固定业务状态。修复目标是通过公开接口在终止前确认正在进行的第 1 次调用，给予该终止场景合理的调用时间，仍使用真实租约过期恢复，并在最终回读中保留第 1 次 interrupted、第 2 次 succeeded、两次请求及原图身份不变的断言；不修改 runtime 或增加盲目重跑。

### 并行机制适配结果

无冲突同步至 75481152，保留新的 unit/e2e CI 矩阵、pytest-xdist 和根/harness 锁文件。终止恢复用例使用同一个 10 秒调用预算传给子进程及恢复服务，在终止前通过 task/get 确认第 1 次请求仍在运行；恢复允许真实租约在首次查询之前或之后过期，最终 interrupted/succeeded、未知首次用量、两次请求和原图引用检查均保留。既有短超时失败测试和生产超时/租约算法未改动。

执行 uv run --no-sync python -m pytest -n 2 -q 的图片采集、生命周期、诊断、向导、CLI、召回权重、证据/恢复和并发审计集合，117 项通过（95.23 秒）。根与 e2e/bub 的 uv lock --check、API/JS/Desktop 生成检查通过；完整非类型 prek 和 Linux 目标 ty 通过，提交前再核查这四个已修改文件。Windows 原生全库 ty 的既有 POSIX 限制保留。

同一草稿 PR 使用精确 force-with-lease 更新，要求远端仍为 90902943，公开说明改为当前基线和独立的新 CI 状态。推送和 GitHub head/可合并状态回读保存在忽略目录 .powercontext/image-source-experiment/pr1956-conflicts-20261013/；旧 head 的 32 项成功不充当新 head 的完成结果。后续以新提交的 CI 为准，继续独立的真实 Atomic Memory/新桌面召回、配对恢复与完整复审；本轮不标记 ready。

## 2026-10-13：修复附件队列审查意见并完成新提交 CI

本轮目标：修复 PR #1956 两条附件队列恢复意见，先用回归复现缓存满后停止读取、关闭项目占据投递页的失败，再实现有界且遵守原授权的恢复；读取 controlled-cli 的实际失败证据，修复根因；更新同一草稿并等待新 head 的全部 CI 结束。用户已明确授权修复、推送和核查全绿，不进行合并或标记 ready。

前置状态：实验 worktree 干净，分支/PR head 为 f71748a6，GitHub 确认 MERGEABLE。当前 CI 共 37 项，35 成功、controlled-cli 失败、installed-package 按条件跳过。失败为第二组 ZCode 受控验收 late_write_not_observed_as_unknown，具体原因以原始日志及 artifact 为准；先保留分支快照和忽略目录中的证据。两条意见对应客户端缓存/调度，不改变服务端 Source 身份、图片协议、默认关闭和停用期间不补采的边界。

### 已实施的修复与本地证据

- 建立 codex/backup-pr1956-before-queue-fix-20261013。真实 CI 日志与两组验收 artifact 存放在忽略目录 .powercontext/image-source-experiment/pr1956-queue-ci-20261013/。
- 增加三个容量恢复场景和两个跨项目投递场景，正确的宿主表示 fixture 下五例在旧代码失败。容量不足保持 watching 并停止本轮追加暂存，先让投递释放空间；读取旧 image_outbox_full 错误游标，且清理不移除仍可恢复的游标。容量不变、游标/字节同事务和正式摄取幂等键保持生效。
- Outbox.pending 接收原有 worker 授权规则作为 predicate，在最多 128 条任务元数据上判定后才分页读取最多八个附件 BLOB；发送前保留第二次授权检查。关闭/更换地址的项目保留积压，其他启用项目可以前进。旧暂存材料重新授权后可投递，关闭期间的未暂存消息不会回读；Outbox 仍为版本 2。
- ZCode 的失败 artifact 显示 Server 已接受且 gate 已释放，但读取时 capture 仍为 running。验收提前释放响应，把 CLI 返回当成 Hook 已完成。改为在原总预算和 10 秒观察预算内保持响应 gate，等 Hook 记录进入终态后再释放；未知结果、唯一 Source、精确接收身份和只读诊断断言保留。
- 两个确定性的协议场景通过脚本加载 HEAD 的原函数复现提前释放错误；新回归既验证 CLI 返回后的异步状态，也验证 failed 不能冒充 unknown。直接场景不建立真实宿主证明，新 head 的两组实际 CLI 验收继续由 CI 执行。
- 两个修改文件集合的 26 项测试通过；扩大为 Codex 采集/投递/观察/生命周期、诊断、CLI、ZCode 协议/宿主及 evaluation 离线集合，执行 uv run --no-sync python -m pytest -n 2 -q，133 项通过。集合有重叠，不累计宣称独立总数。
- Ruff、1260 文件格式检查、Linux 目标全库 ty、根 lock check、ZCode pin check 和差异检查通过。当前代码索引 query_code 返回 code_disabled，未更改 Scope 或启动索引，继续使用本地源码证据。没有操作真实 Desktop、现有实验服务或私有模型配置。

提交前执行全量非类型 prek；原生 Windows 全库 ty 的既有 POSIX 限制仍通过独立 Linux 目标检查区分。推送同一草稿后以实际新 head 的完整 CI 判断结果，最终检查清单写入该忽略目录的 ci-verified.json；旧 head 的成功结果不计入新 head。本次队列与 CI 修复不替代真实 Atomic Memory/新桌面召回、配对恢复及最终完整复审。

## 2026-10-21：权限事务与 Desktop 生成契约集成

本轮目标属于主干集成维护：将 PR #1956 rebase 到 master 2d11abd6，保留本地写入 Owner 的原子事务、Handoff 提交及图片服务初始化，从合并后的 OpenAPI 生成 Python、JavaScript 和 Desktop 绑定，验证图片证据、权限与运行时契约后更新同一 PR。用户已明确授权解决冲突和推送；PR 保持 Ready for review，不执行合并。使用独立 worktree、临时测试数据库与资产，不接触现有桌面服务或真实模型配置。

### 实施与验证

- 原分支 072bcfc8 保存为 codex/backup-pr1956-072bcfc8；13 个提交全部保留并重放到 2d11abd6。运行时初始化同时保留 Owner 写入事务、Handoff committer 和图片服务；Desktop 操作摘要由合并后的 OpenAPI 重新生成。
- 运行 Python 与 JavaScript 生成器、Desktop 官方生成器；Desktop 使用本机已有 Java 21，生成的 Rust 模型保持与主干一致。DSH 官方 build 成功且已提交 bundle 无差异。
- 使用项目固定的 uv 0.13.4 重新生成根和 e2e/bub 锁文件，补齐 images extra 继承的 httpx 依赖；锁格式为 revision 5，未额外升级包版本。
- `uv run --no-sync python -m pytest -n 2 -q`：Access 权限回归、图片证据／解析／恢复／产品／Dashboard、Topic Memory processing 和 API／JS／Desktop 契约集合 237 项通过。
- `uv run --no-sync python -m pytest -q`：Atomic Memory flush 并发、调度 Scope 选择、Topic Memory 恢复、Atomic Memory reconciliation、系统 CLI 和图片诊断集合 156 项通过。
- `pnpm --dir desktop lint`、`test`、`build` 通过，含 32 项 UI 测试。DSH build、API／JS 生成检查、根／harness lock 检查、集成 guidance／capability 文档检查通过。
- 全量非类型 pre-commit hooks、`ty check --python-platform linux` 和 `git diff --check` 通过。Linux 目标类型检查不等于 Windows 原生全库检查。

本轮证据为本地自动化测试、受控 provider 与构建；没有重新验收真实 Desktop 上传、真实模型、新桌面会话召回、跨平台采集生命周期或配对数据库／资产恢复。阶段验收状态不变。更新同一 Ready for review PR 后，以新 head 的 GitHub CI 和后续审查为准，不沿用旧提交的 CI 结论，也不执行合并。


## 2026-10-22：隔离安装、主干同步与完整代码审查

本轮目标属于集成维护：将 PR #1956 rebase 到 upstream/master 0bc42d0b，修复最新 P1 的 builtin/images 安装依赖和 CI，使用 Open Code Review 审查并修复有效问题，再提交更新同一 PR。用户已明确授权 rebase、提交和推送；不执行合并。使用独立 worktree pr1956-fix-1022 和临时测试数据库、资产，不接触现有运行服务。修复前 Python 3.11 的真实 wheel 隔离安装已复现 platformdirs 导入失败；后续验证覆盖修复后的隔离安装、冲突涉及的行为及生成契约。

### 实施与验证

- 14 个提交重放到 master `0bc42d0bc978ded22db1901305280c5b840d6533`。冲突解决保留主干的 Source 元数据压缩、Scope CLI 和 Windows bootstrap receipt 修复，同时保留图片证据投影；Desktop 摘要使用官方生成器生成。
- builtin extra 补充直接导入所需的 platformdirs；根与 e2e/bub 锁文件同步，没有升级依赖版本。CI 隔离安装矩阵增加 images，沿用实际 Runtime 写入、关闭、重开和搜索检查，没有删除原检查。
- 从真实 wheel 在四个独立环境运行 `python -I scripts/ci_role_smoke.py`：Python 3.11、3.14 的 builtin 和 images 均通过。依赖安装不借用开发环境。
- 冲突相关 Atomic Memory 证据／投影、图片 CLI、API／JS／Desktop 契约及图片证据集合 83 项通过；图片投影元数据断言补充后，相关 14 项通过。
- 扩展行为集合共 567 项，首次 545 通过、6 跳过、16 因 Windows 默认临时目录路径超过 260 字符失败；改用短 `--basetemp` 重跑这 16 项全部通过，没有更改生产逻辑或放宽断言。
- 生命周期错误先由回归测试复现，再修复：watcher 启动失败不影响 Session 绑定和 bootstrap；监督进程对临时配置／启动错误退避恢复；注册失败恢复原授权及 epoch。相关采集／投递／观察／生命周期／诊断／标准集成集合 100 项通过，模拟管理器故障不等于实际 macOS／Linux 验收。
- 普通 setup 的默认 false 不再撤销已保存授权，显式关闭标志仍生效；远程客户端向导保留已有选择，双语文档同步。CLI/setup/诊断集合 136 项通过，新增错误身份／摘要参数检查与 setup 组合 25 项通过。
- 最终向导、图片 CLI 和生命周期集合 60 项通过，包含客户端向导回车保留已保存图片选择的回归；这些集合有重叠，不合计宣称独立测试总数。
- Dashboard 避免重复证据链接，材料编号只计可读 Source，关闭或未就绪时的空态提供配置指引。Dashboard 及 Profile/Handoff 集合 92 项通过；空态修改后图片 Dashboard 的 5 项再次通过。
- Desktop lint、32 项 UI 测试、build 通过；DSH 官方 build 无 bundle 漂移。Python／JS／Desktop 生成契约、集成 guidance／capability 文档、两份 lock 检查通过。全量非类型 prek 和 `ty check --python-platform linux` 通过；Windows 原生全库 ty 仍有主干既有 POSIX API 类型限制。

### Open Code Review

执行完整范围 `ocr review --audience agent --from upstream/master --to HEAD`，会话 `e5bfb18a-c93c-4605-9702-c14462f50679`。选择结果为 130 个差异文件，其中 72 个可审查文件、58 个按插件默认规则排除的文档／测试／锁文件等。外部模型完成 56 个，16 个因工具轮次或超时未完成；这 16 个使用插件 delegate preview/rule 流程逐项阅读完整差异与相关上下文补审。可审查文件最终 72/72 已覆盖、0 跳过；本次修复差异另作 delegate 复查。逐文件清单、原始结果和规则保存在工作树忽略目录 `.powercontext/ocr-*`，不把外部模型摘要中的“72 reviewed”误称为外部模型全部完成。

17 条原始意见已逐条核实。有效修复包括上述生命周期、授权、Dashboard、CLI 参数和向导默认值；本地损坏的 workspace profile 可由显式 setup/disable 修复。注册失败后残留的原生登记不会获得新授权，关闭失败也不会恢复已撤销授权；原生管理器不可用仍明确报错并允许后续重试。未采纳移除 context Source 读取权限的建议：合同要求同 Scope 已有 Source 且有读取权限，两次检查对应不同权限。单 Worker 预算、未承诺的内部脚本 status 命令及已通过 Ruff 的导入布局不作无关变更。

本轮没有重新执行真实 Desktop 上传、真实模型推理、新会话召回、配对数据库／资产恢复或原生 macOS／Linux 生命周期。既有验收限制保持明确；本地模拟与类型检查不替代这些验证。推送同一 Ready for review PR 后检查新 head 的 GitHub CI，不沿用旧 head 的结果，也不执行合并。
