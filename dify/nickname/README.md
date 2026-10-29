# 黑历史封号机

Dify Chatflow + PowerContext 文字互动应用。玩家讲述糗事，应用读取该玩家的旧经历和称号，生成新称号与判词，再将本轮经历和生成结果写入 PowerContext。相同用户开启新对话后，仍可检索其历史；不同用户使用独立 Scope。

## 1. 流程与用户隔离

用户输入 → 用户空间准备 → 创建用户空间 → 解析用户空间 → 读取历史 → 整理历史 → LLM → 准备保存 → 保存记忆 → 保存检查 → 直接回复

- Dify 使用系统变量 `sys.user_id`、`sys.app_id` 和环境变量 `PC_NAMESPACE` 生成稳定的创建幂等键。
- 幂等键为 `nickname-user-` 加上 `[namespace, app_id, user_id]` 的规范 JSON 字符串的 SHA-256。哈希不是 Scope ID；真正的 ID 由 `POST /v1/scopes` 返回。
- 同用户新对话复用相同创建请求，不将会话 ID、时间戳或本轮输入放入幂等键。不同用户的创建键不同。
- 读取历史和准备保存都引用“解析用户空间 → scope_id”。没有固定玩家 Scope 环境变量。
- LLM 的 USER 消息显式引用 PowerContext 历史与本轮输入；提交版关闭 LLM 内置会话记忆。
- 保存正文包含 `nicknamehistory`、玩家自述、生成的称号与判词。保存检查使用写入节点的状态码及非空 `records` 判断是否成功。

## 2. 文件与环境

| 文件 | 用途 |
| --- | --- |
| `dsl/nickname.yml` | 可导入的最新 DSL |
| `dsl/nickname.ifpkg` | 与 YAML 内容一致的应用包，manifest 的大小和 SHA-256 已同步 |
| `deploy/powercontext.env.example` | PowerContext 配置模板，只有令牌占位值 |
| `scripts/init_demo.py` | 可选的独立接口诊断，不负责应用内的用户 Scope 映射 |
| `evidence/verification.md` | 实际验证过程、记录对应关系、证据范围 |
| `evidence/observations.json` | 从原始截图转录的关键观测及截图摘要 |
| `evidence/*.png` | A 首轮、A 后续召回及 B 首轮的八张原始截图 |
| `evidence/package-checks.json` | 本地静态检查与代码节点模拟检查结果 |

本地 Dify 为 1.18.0，模型为 `deepseek-flash`，供应商插件为 `langgenius/deepseek:0.0.26`（完整插件标识见 DSL 的 `dependencies`）。模型服务地址、API Key 在导入环境的模型供应商设置中配置。PowerContext 使用本机 HTTP 服务及 SQLite 配置；实际服务包版本没有在本次截图中显示，复现时需使用支持下述三个 `/v1` 接口的服务。初始化脚本只使用 Python 3 标准库。

新环境先按 [Dify Docker 部署说明](https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose) 和 [PowerContext 安装说明](https://powercontext.oceanbase.io/en/docs/get-started/install-and-run/) 安装服务。本项目不需要修改两者源码。

## 3. 启动本地服务

已有实例沿用原配置和数据。Dify：

```bash
sudo systemctl start docker
cd ~/apps/dify/docker
sudo docker compose up -d
```

PowerContext 在另一个终端前台运行：

```bash
export PATH="$HOME/.local/bin:$PATH"
cd ~/apps/powercontext
powercontext server run --env-file .env
```

新 PowerContext 实例可复制 `deploy/powercontext.env.example` 到实际启动目录的 `.env`，填写数据目录并生成令牌。已有配置不要覆盖。令牌可用 `openssl rand -hex 32` 生成，只写入实际 `.env` 和 Dify Secret 环境变量，不写入提交模板。

健康检查：

```bash
curl --noproxy '*' -i --max-time 5 http://127.0.0.1:18000/health/ready
```

Dify 控制台为 [http://localhost:8080/apps](http://localhost:8080/apps)。Docker 中的 Dify 需要访问宿主机 PowerContext，先查询网桥地址：

```bash
sudo docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
```

本机地址为 `172.17.0.1`。在 Dify 的 `docker/.env` 中设置 `SSRF_PROXY_ALLOW_PRIVATE_IPS=172.17.0.1/32`，然后在该目录执行：

```bash
sudo docker compose up -d --no-deps --force-recreate ssrf_proxy
sudo docker compose exec -T ssrf_proxy printenv SSRF_PROXY_ALLOW_PRIVATE_IPS
```

地址以实际环境为准。PowerContext 应监听容器可达的地址，本模板为 `0.0.0.0:18000`。

## 4. 导入与配置

在工作室导入 `dsl/nickname.ifpkg` 或 `dsl/nickname.yml`，二选一。配置模型供应商后，填写应用环境变量：

| 名称 | 类型 | 配置 |
| --- | --- | --- |
| `PC_BASE_URL` | String | Dify 容器可达的服务地址，例如 `http://172.17.0.1:18000`，末尾不加 `/` |
| `PC_TOKEN` | Secret | 实际 PowerContext 令牌，默认值为空；不带 `Bearer`、空格、换行 |
| `PC_NAMESPACE` | String | 本次验收为 `nickname-final-20261029-01`；新验收可换一个未使用过的值，整组测试期间固定 |

三个 HTTP 节点统一使用 Bearer 鉴权并绑定 `PC_TOKEN`：

| 节点 | 方法与地址 | Body 来源 |
| --- | --- | --- |
| 创建用户空间 | `POST {{#env.PC_BASE_URL#}}/v1/scopes` | 用户空间准备的 `request_body` |
| 读取历史 | `POST {{#env.PC_BASE_URL#}}/v1/memory/search` | 动态 `scope_id`、`query: nicknamehistory`、`limit: 3` |
| 保存记忆 | `POST {{#env.PC_BASE_URL#}}/v1/memory/remember` | 准备保存的 `request_body`，动态 Scope 与读取一致 |

导入到另一个应用或环境时会自动创建对应的用户空间。验证截图中的 Scope ID 仅供追踪证据，不能作为所有玩家的共享配置。

## 5. 使用与复现

发布应用，从“访问入口”打开 Web App。若自动生成的地址省略了本机端口，应补上 `:8080`，例如 `http://localhost:8080/chat/<该环境生成的标识>`。导入后的访问标识由 Dify 生成。

1. 用一个新的 `PC_NAMESPACE` 开始验收。用户 A 输入：`我把周报文件命名为“周报_海盐柠檬1029”，结果发错了群。` 首次搜索应为空，生成后写入成功。
2. 同一用户在同一入口开启新对话，输入：`今天开会又忘记共享屏幕了。` 应检索到首轮真实写入的记录，并将该记录传给模型。
3. 使用不同系统用户测试 B。公开 Web App 可用独立无痕窗口模拟匿名新玩家，输入：`我把咖啡倒进了泡面，吃完才发现。` 本次 Scope 应与 A 不同，首次搜索应为空。
4. 从工作室应用管理页的“日志”打开对应对话及回复的追踪，查看用户空间准备、读取历史、保存记忆的输入输出。编辑器预览与正式访问日志分开，正式验收使用 Web App 或 API。

实际截图与首轮写入/后续召回的同一 Artifact 标识见 [验证记录](evidence/verification.md)。本包不携带 PowerContext 数据库；其他机器按流程生成自己的真实数据。

## 6. 辅助脚本

本脚本仅用于独立检查鉴权及接口，不是正式工作流的初始化步骤，也不需要把脚本返回的 Scope ID 填入应用：

```bash
python3 scripts/init_demo.py --url http://127.0.0.1:18000 --env-file "$HOME/apps/powercontext/.env" --empty
```

默认生成唯一名称的空白诊断 Scope；`--seed` 可选写入“内存限定工程师”故事。`--name` 指定幂等键，同名已有游戏记录时空白模式会报错。令牌优先读取同名进程环境变量，否则读取配置文件；换过令牌时注意不要保留旧的进程环境变量。恢复应用验收空白状态应更换一次 `PC_NAMESPACE`，不必删除数据库。

## 7. 提交与本包修正

最终 YAML 和 `.ifpkg` 均不含 `PC_TOKEN` 实际值，模型供应商凭据需在导入后配置。不要提交实际 `.env`，公开旧令牌应在实际服务中轮换；删除导出值不等于撤销旧令牌。

整理时在用户导出包基础上修正两处：保存检查的状态码改为来自“保存记忆”；将原 LLM 的当前消息模板移至显式 USER 消息，并将 `memory` 置空。用户 Scope 映射、HTTP 地址与鉴权绑定保持原导出配置。manifest 已按更新后的 YAML 重算。

截图来自用户实际运行的导出前实例；本次修正另通过本地代码节点和变量绑定模拟检查，没有把模拟结果称为新的真实 Dify 运行。具体检查见 `evidence/package-checks.json`。

更新比赛仓库中本作品的文件，移出旧的重复应用包，再提交推送：

```bash
git add README.md deploy/powercontext.env.example scripts/init_demo.py dsl/ evidence/
git diff --cached --stat -- .
git commit -m "fix(dify): isolate player memory and include verification evidence" -- .
git push origin HEAD
git rev-parse HEAD
```

以上命令在比赛仓库的本作品 `nickname/` 目录执行。将新 commit hash 填入测评平台。

## 8. 已知限制

- 这是由 Dify 选择 Scope 的应用层用户隔离，不代表 PowerContext 已为每个终端用户部署独立认证和 ACL。共享服务令牌仅用于可信应用侧。
- 用户身份以 Dify 的 `sys.user_id` 为准。匿名用户清除浏览器数据或更换浏览器可能被视为新玩家，不保证跨设备身份合并。
- 标记检索最多三条历史，不保证时间排序和全部历史召回。
- 模型文案具有随机性；写入连接失败会中断流程，未实现幂等写入重试或失败补偿。
