import { pipeline, env } from '@huggingface/transformers'
env.backends.onnx.wasm.wasmPaths = '/ort/'

const MODEL = 'Xenova/all-MiniLM-L6-v2'
const DTYPE = 'q8'

let extractor = null
let backend = null

async function load(device) {
extractor = await pipeline('feature-extraction', MODEL, { device, dtype: DTYPE })
backend = device
}

self.onmessage = async (e) => {
const { type, payload } = e.data
try {
if (type === 'init') {
try { await load ('webgpu') }
catch (err) { console.warn ('[worker] WebGPU 失败，降级 WASM：', err?.message || err); await load ('wasm') }
self.postMessage ({ type: 'ready', backend })
} else if (type === 'embed') {
const t0 = performance.now ()
const out = await extractor (payload.text, { pooling: 'mean', normalize: true })
self.postMessage ({ type: 'result', vector: Array.from (out.data), dim: out.dims, elapsed: (performance.now () - t0).toFixed (1) })
}
} catch (err) { self.postMessage ({ type: 'error', message: String (err?.message || err) }) }
}