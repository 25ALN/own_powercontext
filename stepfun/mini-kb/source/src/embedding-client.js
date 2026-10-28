import { detectGPU } from './backend.js'

export class BrowserEmbedding {
  constructor(onProgress) {
    this.onProgress = onProgress
    this.pending = new Map()
    this.sequence = 0
    this.createWorker()
  }

  createWorker() {
    this.worker = new Worker(new URL('./embedding-worker.js', import.meta.url), { type: 'module' })
    this.worker.onmessage = ({ data }) => {
      if (data.type === 'progress') return this.onProgress(data.message)
      const task = this.pending.get(data.id)
      if (!task) return
      this.pending.delete(data.id)
      if (data.type === 'error') task.reject(new Error(data.message))
      else task.resolve(data)
    }
    this.worker.onerror = (event) => {
      const error = new Error(event.message || 'Embedding Worker 加载失败')
      for (const task of this.pending.values()) task.reject(error)
      this.pending.clear()
      this.onProgress(error.message)
    }
  }

  request(type, payload) {
    return new Promise((resolve, reject) => {
      const id = ++this.sequence
      this.pending.set(id, { resolve, reject })
      this.worker.postMessage({ id, type, payload })
    })
  }

  async init({ preferGPU = detectGPU(), dtype = 'q8', warmup = true } = {}) {
    const started = performance.now()
    let fallbackReason = null
    let result
    try {
      result = await this.request('init', { preferGPU, dtype, warmup })
    } catch (error) {
      if (!preferGPU) throw error
      fallbackReason = error.message
      console.warn('WebGPU 初始化失败，重新创建 Worker 并使用 WASM：', error.message)
      this.onProgress('WebGPU 初始化失败，正在加载 WASM…')
      // Transformers.js 3 caches a rejected wasmInitPromise after GPU failure.
      // A fresh Worker is necessary; retrying pipeline() in the same realm fails.
      this.worker.terminate()
      this.createWorker()
      result = await this.request('init', { preferGPU: false, dtype, warmup })
    }
    this.backend = result.backend
    this.embedding = result.embedding
    this.info = { ...result, fallbackReason, readyMs: performance.now() - started }
    return this.info
  }

  async embed(texts) {
    const started = performance.now()
    const result = await this.request('embed', { texts })
    this.lastTimings = { workerMs: result.elapsed, totalMs: performance.now() - started }
    return result.vectors
  }

  dispose() {
    this.worker.terminate()
  }
}
