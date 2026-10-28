# 验证记录

当前版本读取 PowerContext 中的预置历史并生成称号，不自动保存本轮输入和新称号。
下表是验证步骤和记录模板，实际结果及截图需运行后补充。

## 1. 初始化

先启动 Dify 和 PowerContext，在 nickname 目录执行：

```bash
python3 scripts/init_demo.py
```

脚本默认从 ~/apps/powercontext/.env 读取令牌；配置文件在其他位置时使用 --env-file 指定。
同一名称重复运行会复用 Scope，已有匹配历史时保留现有记录，不重复写入。

将输出的 PC_SCOPE_A 填入 Dify 环境变量，并确认“读取历史”请求体中的 scope_id 也使用该 ID。
关闭 LLM 的内置会话记忆，每个样例从新对话开始。

## 2. 验证样例

| 样例 | 操作 | 预期结果 | 实际结果 |
| --- | --- | --- | --- |
| 读取预置历史 | 输入“今天代码保存了，但忘记推送到远程仓库，重装系统后又全没了。” | HTTP 200；history 包含“内存限定工程师”；回复引用旧称号或旧经历 | 待填写 |
| 新会话读取 | 保持同一 Scope，新建对话，输入“今天把测试报告发错群了。” | 仍能检索同一条预置历史；回复根据新输入生成 | 待填写 |
| 无历史 | 按下方命令创建新 Scope，切换实际搜索使用的 ID，再输入糗事 | hits 为空；history 为空；回复不编造旧经历 | 待填写 |

创建用于无历史验证的 Scope：

```bash
python3 scripts/init_demo.py --name nickname-demo-empty-1 --empty
```

## 3. 实际运行证据

- 测试时间：待填写。
- 使用的 Scope ID：待填写。
- 模型名称：待填写。
- 实际称号和判词：待填写。
- 附件：将检索节点输出、整理历史输出及最终回复截图或脱敏日志放在本目录，并填写文件名。

## 4. 恢复初始状态

```bash
python3 scripts/init_demo.py --name nickname-demo-reset-1
```

使用未用过的新名称可创建一份新的预置历史。将返回 ID 重新绑定到 Dify 的实际搜索请求，再新建对话验证。
后续重置继续更换 --name；新建聊天不会清空 PowerContext 历史。
