import { pipeline, env } from '@huggingface/transformers'
import { EMBEDDING } from '../shared/embedding.js'

env.backends.onnx.wasm.wasmPaths = '/ort/'
let extractor
let backend
let loading
let loadTimings

async function load({ preferGPU, dtype = EMBEDDING.dtype, warmup = true }) {
  if (!['q8', 'fp32'].includes(dtype)) throw new Error('仅支持同模型的 q8 / fp32 对比')
  const started = performance.now()
  const options = {
    dtype, revision: EMBEDDING.revision,
    progress_callback: (event) => {
      if (event.status === 'progress') self.postMessage({
        type: 'progress', message: `正在加载 ${event.file}：${Math.round(event.progress)}%`,
      })
    },
  }
  const device = preferGPU ? 'webgpu' : 'wasm'
  extractor = await pipeline('feature-extraction', EMBEDDING.model, { ...options, device })
  const modelMs = performance.now() - started
  const inferenceStarted = performance.now()
  // A GPU session alone does not prove that model inference works. Exercise the
  // actual graph before publishing ready; a failure triggers a fresh WASM worker.
  if (warmup || preferGPU) await extractor(['Warm up local text embeddings.'], {
    pooling: EMBEDDING.pooling, normalize: EMBEDDING.normalize,
  })
  backend = device
  loadTimings = { modelMs, warmupMs: performance.now() - inferenceStarted, totalMs: performance.now() - started }
  return { ...EMBEDDING, dtype }
}

// Keep initialization/inference sequential even if UI actions overlap.
let queue = Promise.resolve()
self.onmessage = ({ data: { id, type, payload } }) => {
  queue = queue.then(async () => {
    try {
      if (type === 'init') {
        loading ||= load(payload)
        const spec = await loading
        self.postMessage({ id, type: 'ready', backend, embedding: spec, timings: loadTimings })
      } else if (type === 'embed') {
        await loading
        if (!extractor) throw new Error('模型尚未就绪')
        const texts = payload.texts
        if (!Array.isArray(texts) || !texts.length) throw new Error('缺少待向量化文本')
        const started = performance.now()
        const output = await extractor(texts, { pooling: EMBEDDING.pooling, normalize: EMBEDDING.normalize })
        const vectors = output.tolist()
        if (vectors.length !== texts.length || vectors.some((vector) => vector.length !== EMBEDDING.dimension)) {
          throw new Error(`模型输出维度异常：${output.dims.join('×')}`)
        }
        self.postMessage({ id, type: 'result', vectors, backend, elapsed: performance.now() - started })
      }
    } catch (error) {
      self.postMessage({ id, type: 'error', message: String(error.message || error) })
    }
  })
}
