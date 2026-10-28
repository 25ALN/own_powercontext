# mini-kb · OceanBase 2026 Stepfun 赛题

浏览器本地 Embedding + seekdb 数据库内混合检索。支持 TXT/Markdown 导入、知识库切换、Top K 结果与完整原文回读；不调用云端推理 API，Node 后端不生成 Embedding。

## 启动

需要 Node.js 20.12+、npm、Docker Compose；本机验证使用 Node 20.19.0。seekdb 配置约 2 GiB 内存，并需要数 GiB 可用磁盘。

在仓库根目录执行：

```sh
cd stepfun/mini-kb/source
npm ci &&
docker compose up -d --wait &&
npm run dev
```

打开 **http://127.0.0.1:5173**。等待模型就绪，创建知识库，选择 UTF-8 `.txt` / `.md` / `.markdown` 文件并导入，再输入问题检索。首次需要浏览器下载约 23 MB INT8 ONNX 权重及 tokenizer；后续使用浏览器缓存。

WebGPU 优先，模型实际推理校验失败时自动重建 Worker 并降级 WASM；界面显示实际后端和失败原因。当前环境已验证 WASM，硬件 WebGPU 的验证限制见 [TECHNICAL.md](TECHNICAL.md)。

默认 seekdb 为 `127.0.0.1:2881`、用户 `root`、空密码、数据库 `mini_kb`。已有实例可跳过 Compose，复制 `source/.env.example` 为 `source/.env` 后修改连接参数。默认仅监听本机，没有用户鉴权。数据库重启后需重启 Node 服务。

项目 `.npmrc` 已配置 `legacy-peer-deps=true` 和 `ignore-scripts=true`：跳过 seekdb 可选 Embedding peer，以及 Sharp 等依赖的安装编译钩子。`npm ci` 无需额外环境变量；显式 `npm run dev/build` 和 `npm test` 正常运行。

生产模式（仍在 `source/` 中）：

```sh
npm run build && npm start
```

打开 **http://127.0.0.1:3001**。开发与生产模式任选其一；前端与 API 共用端口。停止 Node 按 `Ctrl+C`，停止数据库用 `docker compose stop`；数据库数据保存在 `source/.data/seekdb`。

## 实现范围

- 同一 MiniLM 模型，固定上游 revision；INT8、mean pooling、L2 normalize，输出 384 维浮点向量。
- 每块最多 240 个 UTF-16 单位、重叠 40，保留原文偏移。浏览器批量推理，Node 校验模型协议、向量维度及数量。单篇最大 1 MiB、512 块。
- 每个知识库独立 seekdb Collection，cosine HNSW + IK 全文索引；文档原文和状态也保存在 seekdb。
- `hybridSearch()` 同时执行全文与向量检索，RRF 在数据库内融合排序，应用保持数据库返回顺序。写入后刷新索引再发布文档。
- 两路都过滤知识库及已发布文档 ID，串行保护 SDK 会话参数；切换知识库会丢弃旧查询响应。

## 复现测试

保持开发服务运行，在另一个终端进入 `source/`：

```sh
npm test
npm run test:integration
```

打开 **http://127.0.0.1:5173/test/acceptance.html**，点击“运行”，保持页面前台。该页面复用生产 Embedding Worker 和 API，使用 [40 个固定文档、8 条查询](source/test/fixtures.json)，每组重复 3 次，复用现有 INT8/WASM 权重，输出 Recall@5、实际字节数与哈希及分项性能。FP32/INT8 同轮量化对比使用已有本地权重，在同一页面追加 `?quantization=1` 即可运行，结果与复现条件见技术说明；默认模式继续保留 INT8 全链路测试。测试只删除自己创建的临时知识库；页面结束后可下载完整 JSON。

本机实测原始数据见 [results/acceptance.json](results/acceptance.json)，口径、已测结果和未测限制见 [TECHNICAL.md](TECHNICAL.md)。外网/CDN 首次下载不稳定，本轮冷下载加载耗时标为未测，缓存加载耗时单独记录。

目录：`source/` 为完整应用与唯一新增验收测试页面，`results/` 仅保存小型 JSON 测量结果。`node_modules/`、`dist/`、`.data/`、`.artifacts/` 和模型缓存已通过 `.gitignore` 排除，不属于提交材料。
