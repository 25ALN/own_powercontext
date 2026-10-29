# 黑历史封号机

基于 Dify Chatflow 与 PowerContext 的文字互动应用。玩家讲述糗事，应用读取历史经历和称号，生成新称号及判词，再把本次互动写回 PowerContext。同一个 Scope 可以在新会话中继续使用。

## 1. 实现流程

用户输入 → 读取历史 → 整理历史 → LLM → 准备保存 → 保存记忆 → 保存检查 → 直接回复

- Dify 编排 HTTP、Python 和 LLM 节点，展示回复。
- PowerContext 按 Scope 保存、检索玩家经历及生成结果。
- LLM 结合历史生成中文称号与判词，跨会话历史由 PowerContext 提供。
- 每轮保存 `nicknamehistory` 标记、玩家输入、称号与判词。
- 写入返回 HTTP 2xx 且 `records` 非空时，保存检查显示“记忆已保存，下次新会话仍可读取。”

## 2. 文件与依赖

| 路径 | 用途 |
| --- | --- |
| `dsl/nickname.yml` | 修改后重新导出的 Chatflow DSL |
| `dsl/nickname.ifpkg` | 可选保留的同版本应用包，含 `app.yaml` 和 manifest |
| `deploy/powercontext.env.example` | PowerContext 配置模板 |
| `scripts/init_demo.py` | 创建空白 Scope，可选初始化演示故事 |
| `evidence/verification.md` | 实际截图记录及复现步骤 |
| `evidence/recall.png`、`evidence/reply.png` | 本次提供的实际运行截图 |

本地环境记录为 Dify 1.18.0、PowerContext 1.4.1；脚本仅依赖 Python 3 标准库。原导出包使用 `deepseek-flash` 和供应商插件 `langgenius/deepseek:0.0.26`，最终依赖以新 DSL 的 `dependencies` 和 LLM 节点配置为准。模型 API 地址与凭据在 Dify 模型供应商设置中配置。

## 3. 启动现有服务

已有实例沿用原配置和数据：

```bash
sudo systemctl start docker
cd ~/apps/dify/docker
sudo docker compose up -d
```

另开终端启动 PowerContext，并保持运行：

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/apps/powercontext
powercontext server run --env-file .env
```

新 PowerContext 实例可将 `deploy/powercontext.env.example` 复制为实际配置，设置数据目录并替换令牌占位值。不要覆盖已有 `.env`。服务端令牌与 Dify 的 `PC_TOKEN` 必须一致。

健康检查：

```bash
curl --noproxy '*' -i --max-time 5 http://127.0.0.1:18000/health/ready
```

应返回 HTTP 200。Dify 页面入口为本地部署的 [http://localhost:8080/apps](http://localhost:8080/apps)。

Dify 在 Docker 内、PowerContext 在宿主机时，先查询网桥地址：

```bash
sudo docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
```

例如输出 `172.17.0.1`，则在 Dify 的 `docker/.env` 中设置 `SSRF_PROXY_ALLOW_PRIVATE_IPS=172.17.0.1/32`。修改后在该目录执行：

```bash
sudo docker compose up -d --no-deps --force-recreate ssrf_proxy
sudo docker compose exec -T ssrf_proxy printenv SSRF_PROXY_ALLOW_PRIVATE_IPS
```

地址使用实际查询值；PowerContext 需监听容器可达的地址，本机示例为 `0.0.0.0:18000`。

## 4. 创建验收用空白 Scope

在本作品的 `nickname/` 目录执行：

```bash
python3 scripts/init_demo.py --empty
```

脚本默认从 `~/apps/powercontext/.env` 读取 `POWERCONTEXT_SERVER_AUTH_TOKEN`，也支持同名进程环境变量。不指定名称时自动生成唯一名称，默认不写入预置故事。输出包含真实的 `PC_SCOPE_A` 和匹配历史数量。

其他用法：

```bash
# 自定义服务地址和配置文件
python3 scripts/init_demo.py --url http://127.0.0.1:18000 --env-file /实际路径/.env --empty

# 可选预置“内存限定工程师”故事，不能用作空白验收
python3 scripts/init_demo.py --seed
```

`--name` 可指定名称。同名请求复用创建幂等键；空白模式发现已有游戏历史时会报错，需换新名称。脚本不清空原数据，也不替代工作流运行时的写入。

## 5. 导入与变量绑定

在 Dify 导入新 `dsl/nickname.yml` 或同版本 `.ifpkg`，配置模型供应商凭据，填写应用环境变量：

| 变量 | 类型 | 值 |
| --- | --- | --- |
| `PC_BASE_URL` | String | 容器可达地址，如 `http://172.17.0.1:18000`，末尾不加 `/` |
| `PC_SCOPE_A` | String | 脚本返回的真实 Scope ID |
| `PC_TOKEN` | Secret | 服务端令牌，不带 `Bearer`、空格或换行 |

两个 HTTP 节点均使用 Bearer 鉴权，Token 绑定 `PC_TOKEN`：

| 节点 | 方法和 URL | 请求体 |
| --- | --- | --- |
| 读取历史 | `POST {{#env.PC_BASE_URL#}}/v1/memory/search` | `scope_id` 绑定 `PC_SCOPE_A`，`query` 为 `nicknamehistory`，`limit` 为 `3` |
| 保存记忆 | `POST {{#env.PC_BASE_URL#}}/v1/memory/remember` | 只插入“准备保存 → request_body”变量，不再包引号或大括号 |

“准备保存”的 `scope_id` 同样绑定 `PC_SCOPE_A`，通过 `json.dumps` 序列化输入和回复。“整理历史”输出传给 LLM；最终“直接回复”引用“保存检查 → answer”。验收时关闭 LLM 内置会话记忆。

## 6. 两次新会话验收

1. 创建新空白 Scope，绑定 `PC_SCOPE_A`，新建预览对话，输入：`我把周报文件命名为“周报_海盐柠檬”，结果发错了群。`
2. 确认首次搜索 `hits` 为空；本轮回复生成后，保存节点返回 HTTP 2xx、非空 `records`，正文包含本轮输入和生成称号。
3. 新建另一个对话，保持同一个 Scope，输入：`今天开会又忘记共享屏幕了。`
4. 确认搜索的 `hits[].memory.text` 包含第一轮真实生成的记录，回复使用该称号或经历，第二轮也写入成功。
5. 保存两轮节点输出和回复，记录实际 Scope ID、两个会话 ID、写入记录标识及召回正文，隐藏请求头中的令牌。

现有截图及其证据范围见 [验证记录](evidence/verification.md)。截图中已经存在历史，不能据此确认空白首轮及两轮记录对应关系。

## 7. 重新导出提交

验证后重新导出应用，关闭“导出密钥值”，替换旧应用包及 DSL：

```bash
unzip -l dsl/nickname.ifpkg
unzip -p dsl/nickname.ifpkg app.yaml > dsl/nickname.yml
rg -n 'memory/(search|remember)|PC_BASE_URL|PC_SCOPE_A' dsl/nickname.yml
```

若 `app.yaml` 位于包内子目录，使用列表中的实际路径，manifest 随包保留。确认新 DSL 含写入节点，URL、读写 Scope 和鉴权均绑定变量，不提交实际令牌。本文和脚本的更新不能替代重新导出本机验证后的 DSL。

## 8. 当前限制

- 单玩家使用一个固定 Scope，未实现多用户登录与自动隔离。
- 固定标记检索最多三条历史，不保证时间排序或完整召回。
- 模型文案具有随机性，应以原始记录核对历史引用。
- HTTP 连接失败会使流程失败，未实现失败补偿或幂等重试。
- 新建聊天不删除历史，恢复空白状态需创建新 Scope。
