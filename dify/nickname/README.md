# 黑历史封号机

基于 Dify Chatflow 与 PowerContext 的轻量文字互动应用。玩家讲述一件糗事，应用检索该玩家记忆空间中的旧经历和旧称号，再生成一个新称号及一句幽默判词。

**当前提交版本是“读取预置历史版”**：历史通过初始化命令写入 PowerContext；Chatflow 负责读取历史并生成回复。本轮新输入和新称号尚未自动写入 PowerContext。

## 1. 玩法与技术分工

预置历史示例：

> 玩家曾经写了一下午代码，关机后发现没有保存，被封为“内存限定工程师”。

玩家在新对话中输入：

> 今天代码保存了，但忘记推送到远程仓库，重装系统后又全没了。

应用检索旧记录，并结合本次经历生成新的称号。以下仅为输出形式示意，实际措辞由模型生成：

~~~text
称号：持久化绝缘体
判词：从“内存限定工程师”晋升至此，你终于说服了硬盘，却还没通知远程仓库。
~~~

| 组件 | 在作品中的实际作用 |
| --- | --- |
| Dify | 接收输入，编排 HTTP 请求和代码节点，调用文本模型，展示结果 |
| PowerContext | 持久化保存演示历史，按固定 Scope 检索，为生成回复提供历史素材 |
| 文本模型 | 根据本次糗事和检索到的历史生成称号、判词 |
| SQLite | PowerContext 的本地持久化存储，由 PowerContext 管理 |

当前工作流：

**用户输入 → 读取历史（HTTP）→ 整理历史（Python）→ LLM → 直接回复**

本作品选择 PowerContext 作为指定技术的使用方式；不使用 seekdb，也不使用知识库、Embedding 或 Rerank 服务。

## 2. 环境与依赖

| 项目 | 版本或要求 |
| --- | --- |
| 操作系统 | Linux；下文安装命令以 Arch Linux 为例 |
| Dify | 1.18.0，官方 Docker Compose 部署 |
| Docker Compose | 2.24.0 或更高 |
| PowerContext | 1.4.1，通过 uv 安装 |
| PowerContext 的 Python | 3.12 |
| 模型 | 当前实例使用的模型名称为 deepseek-flash |
| 模型供应商插件 | **【提交前填写：插件名称及精确版本，按导出 DSL 的 dependencies 和实际安装记录核对】** |
| 模型 API Base URL | **【提交前填写：实际模型服务地址，不包含 API Key】** |
| 自定义插件、独立前后端 | 无 |

模型名称以实际供应商提供的模型列表为准。重新部署时，需要配置支持相应模型的供应商、API Key 和 Base URL；如改用其他模型，应记录实际供应商、插件版本及模型名称。

资源与网络条件：

- Dify 官方最低要求为 2 核 CPU、4 GiB 内存；同时运行 PowerContext 时需为其预留资源。
- 模型通过远程 API 调用，本应用不要求本地 GPU。
- 安装过程需要访问 GitHub、Python 包源和容器镜像源。
- Dify 的模型供应商插件需要能够访问模型 API。
- Dify 的 HTTP 请求节点需要能够访问 PowerContext。
- 下文使用本机端口 8080 提供 Dify 页面，18000 提供 PowerContext HTTP 服务。

## 3. 提交文件与应用包

以下路径相对于本作品的 nickname 目录：

| 路径 | 用途 |
| --- | --- |
| README.md | 本运行说明 |
| dsl/nickname.yml | 可独立导入的 Chatflow DSL |
| dsl/nickname.ifpkg | 可选保留的 Dify 原始应用包 |
| deploy/ | 部署配置模板存放目录 |
| scripts/ | 初始化等辅助脚本存放目录 |
| evidence/ | 实际验证记录、截图或脱敏日志 |

本说明给出完整的手动初始化命令，不依赖尚未提供的辅助脚本。

Dify 1.18 导出的 .ifpkg 是 ZIP 格式的应用包，包含 app.yaml 和配套清单等文件。manifest 随原包保留即可；单独导入 DSL 时，使用 app.yaml 的内容，不要把 manifest 拼入 YAML。

如需提取 DSL，先将导出包放到 dsl/nickname.ifpkg，再在 nickname 目录执行：

~~~bash
unzip -l dsl/nickname.ifpkg
unzip -p dsl/nickname.ifpkg app.yaml > dsl/nickname.yml
~~~

如果压缩包中的 app.yaml 带有目录前缀，以第一条命令列出的实际路径为准。

导出时关闭“导出密钥值 / Export secret values”。模型凭据和 PowerContext 令牌在导入环境中重新配置。若同时提交 .ifpkg 和 .yml，两者应来自同一份已验证的工作流。

## 4. 安装并启动 Dify

已有可用的 Dify 1.18.0 实例时，可以跳过本节。以下命令用于新安装，已有实例应保留自己的配置和数据。

### 4.1 安装基础工具

~~~bash
sudo pacman -Syu --needed docker docker-compose git uv curl openssl unzip python
sudo systemctl enable --now docker
sudo docker compose version
~~~

### 4.2 下载固定版本并配置

~~~bash
mkdir -p "$HOME/apps"
git clone --depth 1 --branch 1.18.0 https://github.com/langgenius/dify.git "$HOME/apps/dify"
cd "$HOME/apps/dify/docker"
cp .env.example .env
openssl rand -hex 32
~~~

编辑当前目录的 .env：

~~~dotenv
DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN=REPLACE_WITH_THE_GENERATED_SANDBOX_TOKEN
EXPOSE_NGINX_PORT=8080
EXPOSE_NGINX_SSL_PORT=8443
NEXT_PUBLIC_SOCKET_URL=ws://localhost:8080
~~~

将沙箱令牌占位值替换为刚生成的随机字符串。

### 4.3 启动与创建账号

~~~bash
sudo docker compose up -d
sudo docker compose ps
~~~

打开 http://localhost:8080/install 创建管理员账号。已有管理员账号时，直接访问 http://localhost:8080 登录。

进入“集成 / 模型供应商”，按第 2 节记录安装模型供应商插件并配置模型服务。

## 5. 安装并启动 PowerContext

### 5.1 安装

~~~bash
uv tool install --python 3.12 "powercontext[cli,server]==1.4.1"
export PATH="$HOME/.local/bin:$PATH"
mkdir -p "$HOME/apps/powercontext"
cd "$HOME/apps/powercontext"
~~~

### 5.2 生成配置

已有 PowerContext 配置时沿用原文件，跳过下面的生成步骤。以下命令为新实例创建配置，并自动生成随机访问令牌：

~~~bash
python3 - <<'PY'
from pathlib import Path
import secrets

config = Path(".env")
if config.exists():
    raise SystemExit(".env 已存在，请沿用现有配置，未覆盖文件。")

data_dir = Path.home() / "apps" / "powercontext" / "data"
lines = [
    f"POWERCONTEXT_HOME={data_dir}",
    "POWERCONTEXT_SERVER_DATABASE_KIND=sqlite",
    "POWERCONTEXT_SERVER_HTTP_HOST=0.0.0.0",
    "POWERCONTEXT_SERVER_HTTP_PORT=18000",
    "POWERCONTEXT_SERVER_ACCESS_MODE=enforced",
    f"POWERCONTEXT_SERVER_AUTH_TOKEN={secrets.token_hex(32)}",
]
config.write_text("\n".join(lines) + "\n", encoding="utf-8")
config.chmod(0o600)
print("已生成配置文件。")
PY
~~~

### 5.3 启动与检查

~~~bash
powercontext server run --env-file "$HOME/apps/powercontext/.env"
~~~

保持这个终端运行，在另一个终端检查：

~~~bash
curl --noproxy '*' -i --max-time 5 http://127.0.0.1:18000/health/ready
~~~

预期返回 HTTP 200，正文包含 status 为 ready。接口文档入口为 http://localhost:18000/docs。

默认数据库保存在配置的 POWERCONTEXT_HOME 目录中。重启服务后可继续读取已有数据。当前显式记忆写入与全文检索流程不需要为 PowerContext 配置模型。

## 6. 配置 Dify 到 PowerContext 的网络

本部署中 Dify 运行于容器，PowerContext 运行于宿主机。Dify 节点需要使用容器可达的宿主机地址。

获取 Docker 网桥的宿主机地址：

~~~bash
sudo docker network inspect bridge --format '{{(index .IPAM.Config 0).Gateway}}'
~~~

假设实际输出为 172.17.0.1，则 Dify 使用的 PowerContext 地址为：

~~~text
http://172.17.0.1:18000
~~~

编辑 Dify 的 ~/apps/dify/docker/.env，设置：

~~~dotenv
SSRF_PROXY_ALLOW_PRIVATE_IPS=172.17.0.1/32
~~~

地址以实际查询结果为准。已有其他放行地址时，用英文逗号追加；同名配置只保留一条有效赋值。

重新创建代理：

~~~bash
cd "$HOME/apps/dify/docker"
sudo docker compose --env-file .env up -d --no-deps --force-recreate ssrf_proxy
sudo docker compose exec -T ssrf_proxy printenv SSRF_PROXY_ALLOW_PRIVATE_IPS
~~~

最后一条命令应输出配置的地址范围。PowerContext 的健康接口无需认证，记忆和 Scope 接口需要令牌。

## 7. 初始化演示历史

以下命令在宿主机终端执行。先读取之前由本机创建的 PowerContext 配置：

~~~bash
source "$HOME/apps/powercontext/.env"
~~~

### 7.1 创建演示 Scope

~~~bash
curl --noproxy '*' --fail-with-body -sS \
  http://127.0.0.1:18000/v1/scopes \
  -H "Authorization: Bearer $POWERCONTEXT_SERVER_AUTH_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{
    "title": "nickname-demo-a",
    "summary": "黑历史封号机演示玩家A",
    "idempotency_key": "nickname-demo-a"
  }'
~~~

从响应中记录真实的 scope_id，然后在同一个终端设置：

~~~bash
PC_SCOPE_A='REPLACE_WITH_THE_RETURNED_SCOPE_ID'
~~~

Scope 是这次演示的固定记忆空间。重新创建聊天时继续使用它；Dify 的聊天会话 ID 不用作 Scope ID。

### 7.2 写入预置记忆

整段执行，包含最后一行 EOF：

~~~bash
curl --noproxy '*' --fail-with-body -sS \
  http://127.0.0.1:18000/v1/memory/remember \
  -H "Authorization: Bearer $POWERCONTEXT_SERVER_AUTH_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<EOF
{
  "scope_id": "$PC_SCOPE_A",
  "kind": "decision",
  "text": "nicknamehistory 玩家自述：写了一下午代码，关机后发现没有保存。本次封号：内存限定工程师。判词：硬盘对你一下午的努力毫不知情。"
}
EOF
~~~

预期响应包含非空 records，记录中可找到上述 text。成功后不必重复执行本次写入，以免产生重复演示记录。

### 7.3 检查检索结果

~~~bash
curl --noproxy '*' --fail-with-body -sS \
  http://127.0.0.1:18000/v1/memory/search \
  -H "Authorization: Bearer $POWERCONTEXT_SERVER_AUTH_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary @- <<EOF
{
  "scope_id": "$PC_SCOPE_A",
  "query": "nicknamehistory",
  "limit": 3
}
EOF
~~~

预期响应包含 mode 为 text、非空 hits，以及“内存限定工程师”。

每条历史正文位于 hits 数组元素的 memory.text 字段中。固定标记 nicknamehistory 用于全文检索，搜索结果不保证按时间从新到旧排列。

## 8. 导入应用并重新绑定配置

### 8.1 导入

在 Dify“工作室”中选择创建应用并导入：

- Dify 1.18.0 可导入完整的 dsl/nickname.ifpkg。
- 也可通过 DSL 导入入口导入 dsl/nickname.yml。
- 两种方式选择一种即可，不需要先后导入两份。
- 按导入提示安装应用依赖的模型供应商插件，再配置该供应商的凭据。

### 8.2 环境变量

在应用编排页的“环境变量”中填写：

| 变量 | 类型 | 值 |
| --- | --- | --- |
| PC_BASE_URL | String | Dify 容器可达的 PowerContext 地址，例如 http://172.17.0.1:18000 |
| PC_TOKEN | Secret | PowerContext 配置中 POWERCONTEXT_SERVER_AUTH_TOKEN 的值 |
| PC_SCOPE_A | String | 第 7 节返回的真实 scope_id |

PC_TOKEN 只填写令牌字符，前后不带空格、换行或 Bearer 前缀。

### 8.3 检查“读取历史”节点

| 配置 | 值 |
| --- | --- |
| 方法 | POST |
| URL | PowerContext 地址后追加 /v1/memory/search |
| 鉴权 | API Key → Bearer |
| Token | 通过变量选择器选择环境变量 PC_TOKEN |
| Body 类型 | JSON |
| Body.scope_id | 第 7 节返回的真实 Scope ID |
| Body.query | nicknamehistory |
| Body.limit | 3 |

推荐通过变量选择器将 URL 绑定到 PC_BASE_URL，将 scope_id 绑定到 PC_SCOPE_A。

如果导出的节点仍然使用固定地址或固定 Scope ID，导入后必须在节点中替换成新环境的实际值；仅修改环境变量不会改变写死的请求内容。

Bearer 鉴权会自动生成 Authorization 请求头，无需再手动拼接相同请求头。

### 8.4 检查代码与模型绑定

“整理历史”代码节点的输入：

| 输入名称 | 绑定值 |
| --- | --- |
| body | 读取历史 → body |
| status_code | 读取历史 → status_code |

输出为 String 类型的 history，内容由 hits[].memory.text 合并而成。

LLM 的 USER 消息包含：

- 历史记录：整理历史 → history。
- 本次糗事：用户输入 → query。

“直接回复”节点使用 LLM → text。为便于验证 PowerContext 的作用，关闭 LLM 内置的会话记忆，使用新对话测试。

如果模型输出带有 think 标签的思考文字，可启用“推理标签分离”；该功能只对对应标签格式生效。

## 9. 应用入口与完整验证样例

开发验证入口：在 http://localhost:8080/apps 打开“黑历史封号机”，进入编排页点击“预览”。

如需提供独立体验入口，在 Dify 发布应用后，从“访问入口”复制本次部署生成的 Web App 链接。应用 ID 和访问链接由导入环境生成。

### 样例一：检索预置历史并生成回复

1. 按第 7 节创建 Scope 并写入预置记忆。
2. 按第 8 节在 Dify 中绑定实际地址、令牌与 Scope ID。
3. 新建预览对话，输入：

~~~text
今天代码保存了，但忘记推送到远程仓库，重装系统后又全没了。
~~~

验证以下结果：

| 检查位置 | 预期 |
| --- | --- |
| 读取历史节点 | status_code 为 200；body.hits 包含预置记录 |
| 整理历史节点 | history 包含“内存限定工程师”及旧经历 |
| 最终回复 | 生成称号和判词，并引用旧称号或旧经历 |

模型输出不要求逐字一致；验收重点是检索结果实际进入了模型输入，并影响了回复。

### 样例二：新会话继续读取同一份历史

1. 保持同一个 Scope ID，重新开始一次对话。
2. 发送另一件具体糗事。
3. 检查仍能检索同一份预置历史，并按提示词使用其中的内容。

这验证的是 PowerContext 中预置历史的持久化与跨会话读取。当前版本不会自动记录样例一产生的新称号。

### 样例三：空白 Scope

1. 参考第 7.1 节创建另一个 Scope，使用不同的 title 和 idempotency_key。
2. 不向该 Scope 写入演示记忆。
3. 将 Dify 环境变量以及实际搜索请求中的 scope_id 切换到新 ID。
4. 新建对话并输入糗事。

预期 hits 为空、history 为空，模型生成初始称号，不声称记得旧经历。

### 重复验证与恢复初始状态

创建一个新的 Scope，给创建请求使用新的唯一 idempotency_key，再按第 7.2 节写入一次预置历史，最后更新 Dify 的实际 Scope 配置。

新建 Dify 对话不会清除 PowerContext 数据。重新使用原来的幂等键也不应当作“清空历史”的方式。

### 实际运行证据

将真实运行结果记录在 evidence/verification.md，并保存截图或脱敏日志。至少包含测试输入、实际回复、搜索节点输出和代码节点输出。

本 README 中的回复文本是验收示意，不是实际运行证据。应用导出包不会携带 PowerContext 数据和既有运行日志，因此需要按本说明重新初始化，并单独提交证据。

## 10. 常见问题

| 现象 | 检查方法 |
| --- | --- |
| blocked by SSRF protection | 检查实际宿主机 IP、Dify 的 SSRF_PROXY_ALLOW_PRIVATE_IPS，以及代理容器内的值 |
| Connection refused | 检查 PowerContext 是否运行，是否监听 0.0.0.0:18000 |
| HTTP 401 | 检查 PC_TOKEN 是否与服务端令牌一致 |
| Illegal header value | 删除 PC_TOKEN 首尾的换行和空格，只保留令牌字符 |
| main() got an unexpected keyword argument arg1 | 代码节点左侧输入名称必须为 body 和 status_code，与函数参数一致 |
| 代码节点提示输出缺失 | 检查代码返回字段和输出声明是否都为 history |
| 搜不到演示记录 | 检查实际请求中的 Scope ID、初始化写入是否成功，以及固定标记是否一致 |
| 回复没有使用历史 | 检查 history 是否非空，以及 LLM 的 USER 消息是否实际引用该变量 |
| 更换环境变量后请求仍用旧 ID | 检查 HTTP 请求体是否还写着固定 Scope ID，并进行替换或变量绑定 |

## 11. 当前限制

- 当前为单玩家演示，通过一个固定 Scope 关联历史，没有注册、登录和玩家切换功能。
- 当前 Chatflow 只读取历史；新输入和新称号需要显式写入后才会成为后续可检索历史。
- 本应用使用标记全文检索，最多请求 3 条匹配记录，不提供语义检索和时间排序保证。
- 模型文案具有随机性，回复质量取决于模型服务。
- 当前使用 PowerContext 静态令牌访问服务，Scope 用于划分数据；演示配置没有实现生产环境中的独立用户认证。
- 更换部署机器、服务地址或初始化数据库后，需要重新绑定地址、凭据和真实 Scope ID。

## 12. 数据来源与参考

演示糗事、称号和判词为本项目自编测试数据，无外部数据集依赖。工作流使用 Dify 内置节点，通过 PowerContext 公开 HTTP API 集成；本作品不要求修改 Dify 或 PowerContext 源码。

- [Dify 仓库与固定版本](https://github.com/langgenius/dify/tree/1.18.0)
- [Dify Docker Compose 部署](https://docs.dify.ai/en/self-host/deploy/quick-start/docker-compose)
- [Dify 应用导出与导入](https://docs.dify.ai/en/cloud/use-dify/workspace/export-import-apps)
- [Dify HTTP 请求节点](https://docs.dify.ai/en/cloud/use-dify/nodes/http-request)
- [PowerContext 安装与启动](https://powercontext.oceanbase.io/en/docs/get-started/install-and-run/)
- [PowerContext HTTP API](https://powercontext.oceanbase.io/en/docs/develop/http-api/)
- [PowerContext API Quick Start](https://powercontext.oceanbase.io/en/docs/develop/api-quickstart/)
