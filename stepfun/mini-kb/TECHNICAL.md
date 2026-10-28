# mini-kb 技术说明 · Stepfun

## 模型与执行位置

同一 `Xenova/all-MiniLM-L6-v2` 模型，固定 revision `751bff37182d3f1213fa05d7196b954e230abad9`，Transformers.js 3.8.1、ONNX Runtime Web 1.22.0-dev.20250409-89f8206ba4。使用上游训练后动态 INT8/q8 量化 ONNX，未重新训练；实际图含 24 个 DynamicQuantizeLinear、36 个 MatMulInteger，producer 为 onnx.quantize 0.1.0；mean pooling + L2 normalize，输出 384 维浮点向量。文档和查询均在浏览器 Worker 内推理，Node 与 seekdb 不生成 Embedding，不调用云端推理 API。

| 同轮配对指标 | FP32 | INT8/q8 |
| --- | --- | --- |
| 实际文件大小 | **90,387,606 bytes（86.20 MiB）** | **22,972,370 bytes（21.91 MiB）** |
| 文档 Embedding 吞吐量 | **48.40 Chunk/s** | **67.66 Chunk/s** |
| 查询 Embedding 延迟 | **10.60 ms** | **8.05 ms** |
| 纯向量 Recall@5 | **0.475** | **0.475** |

实测文件压缩比（FP32 / INT8）为 **3.9346×**；INT8 − FP32 的 Recall@5 差值为 **0.000（0 个百分点）**，结论仅适用于本固定小样本。

FP32 使用用户提供的 `source/models/fp32/model.onnx`，下载来源为 [hf-mirror 的同模型 main 文件](https://hf-mirror.com/Xenova/all-MiniLM-L6-v2/resolve/main/onnx/model.onnx)。其 SHA-256 为 `759c3cd2b7fe7e93933ad23c4c9181b7396442a2ed746ec7c1d46192c469c46e`，与 [固定 revision 的官方文件](https://huggingface.co/Xenova/all-MiniLM-L6-v2/tree/751bff37182d3f1213fa05d7196b954e230abad9/onnx) LFS 哈希一致；INT8 的 SHA-256 为 `afdb6f1a0e45b715d0bb9b11772f032c399babd23bfc31fed1c170afc848bdb1`，也与该 revision 一致。两份 ONNX 均由实际浏览器 WASM 成功加载并完成推理；FP32 图包含 FP32 权重和 48 个 MatMul。共享 config、tokenizer、tokenizer_config 和 special_tokens_map 的 Git blob ID 均与该 revision 校验一致；mean pooling、L2 normalize 和 384 维输出相同。

本轮复用既有 40 Chunk、8 查询与相关性标注；每种精度 3 个新 Worker，每次相同预热，文档 batch=8、查询逐条运行，精度顺序交替。吞吐量取 3 组中位数，查询延迟取 24 个样本中位数，均排除模型加载和网络时间。**本表 INT8 是同轮重测值**；历史 INT8 的 66.25 Chunk/s、8.95 ms 及全部原始数据保持不变。完整配对原始数据、Tokenizer 指纹与计算结果保存在 [acceptance.json](results/acceptance.json) 的 `quantizationComparison`。

FP32 仅用于评测：评测页校验本地文件后注入浏览器模型缓存，复用现有 Worker 的 fp32 参数；正式 INT8 推理路径及 seekdb API/SQL/索引均保持原样。FP32 端到端查询未测，因为正式 API 仅接受 q8 协议，本轮未扩展它。`source/models/` 已被 Git 忽略。

## WebGPU / WASM 与 seekdb

主路径优先尝试 WebGPU；加载后实际执行一条文本推理，成功才显示就绪。失败后重新创建 Worker 并使用 WASM，避免 Transformers.js 缓存失败初始化 Promise；界面显示真实后端及降级原因。主机虽有 RTX 4070 Laptop GPU，但无头 Chrome 默认与 Vulkan 路径均无法取得 GPUAdapter。**硬件 WebGPU 推理及 INT8 GPU 算子兼容性未测**，不模拟成功，不声称 GPU 加速。本轮只测 WASM。

Node 通过 `seekdb@1.4.0` Server 模式访问本机 seekdb 1.4。每库独立 Collection，384 维 cosine HNSW + IK 全文索引；全文和 KNN 都过滤知识库及已发布文档 ID。`hybridSearch()` 调用数据库内部 RRF（constant=60，候选 `max(20,4K)`），应用保持数据库顺序，无前端/Node 融合。写入原文、向量、文档/知识库 ID 和偏移后刷新索引，成功才标为 ready；切库丢弃旧响应。

## 历史 INT8 全链路实测与复现

固定 40 个单块文档、8 条查询（6 英文、2 中文），每条预先声明 5 个相关文档。纯向量 Recall@5 在浏览器评测页计算单路 cosine 排名，用于观察 Embedding；应用混合检索及其 Recall@5 使用 seekdb 结果，不做应用端融合。每组 3 次，查询共 24 个样本；报告中位数。本样本纯向量英文查询均值 0.60、中文查询均值 0.10；英文模型与极小自编语料的结果不代表通用中文检索质量。

| WASM 指标 | 当前结果 |
| --- | --- |
| 模型首次外网冷下载加载 | 未测：网络不稳定，缓存加载单列 |
| 缓存模型初始化 / 模型就绪（含 Worker 启动及预热） | **411.50 / 538.20 ms** |
| 文档 Embedding（40 块，batch=8） | **603.80 ms，66.25 Chunk/s** |
| 40 个单块文档写入 / 40 次索引刷新 | **322.17 / 23,969.54 ms** |
| 40 文档入库、发布及刷新服务端总计 | **24,498.98 ms** |
| 查询 Embedding / seekdb 混合检索 / 端到端 | **8.95 / 4.06 / 18.00 ms** |
| 优化前后对比 | 未测；按范围收缩要求未进行新的性能优化，批大小维持 8 |

环境：i7-12800HX、16 GiB RAM、Linux、Chrome 156、Node 20.19.0；WASM 无跨源隔离、多线程未启用。缓存加载三次均使用新 Worker；首次外网冷下载未测。写入采用逐文档上传和刷新，每组共 40 次刷新，刷新是本样本主要开销；查询端到端包含 Worker 通信、本地向量化、HTTP 与数据库耗时，不含 DOM 绘制。上表为历史 INT8 数据；量化影响以本轮配对表为准，本轮未进行新的性能优化。

复现：在 `source/` 执行 `npm ci && docker compose up -d --wait && npm run dev`，打开 `http://127.0.0.1:5173/test/acceptance.html` 点击运行并下载 JSON。源码只新增这一个验收页面及固定数据 JSON，复用生产 Worker/API。基础验证命令为 `npm test`、`npm run test:integration`、`npm run build`。缓存、权重、数据库和构建产物不进入交付材料。

既有验证：生产构建通过；3 项单元测试及真实 seekdb 集成测试通过；生产静态服务上的真实浏览器 WASM 完成 Markdown 导入、索引刷新、Top K 检索和 BOM/CRLF 原文回读。仅清理测试自己创建的知识库，既有知识库保留。

量化对比复现：保留已校验的本地 `source/models/fp32/model.onnx`，在 `source/` 执行 `npm run dev`，打开 `http://127.0.0.1:5173/test/acceptance.html?quantization=1` 点击运行并下载 JSON。该模式仅对比浏览器 Embedding，不写入知识库、不执行 GPU 探测。之前的 FP32 网络下载失败记录保留在结果的 `fp32.previousAttempt`。
