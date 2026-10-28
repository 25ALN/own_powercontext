import { detectGPU } from './backend.js'

const $ = (id) => document.getElementById(id)

// 1) 界面先提示浏览器是否暴露 WebGPU（最终以 worker 实际结果为准）
const gpu = detectGPU()
$('gpuHint').textContent = gpu
  ? '检测到 WebGPU 支持，将优先尝试 GPU 推理'
  : '未检测到 WebGPU，将直接使用 WASM（CPU）推理'

// 2) 用 module worker 加载推理逻辑，UI 主线程不被阻塞
const worker = new Worker(new URL('./embedding-worker.js', import.meta.url), { type: 'module' })

worker.onmessage = (e) => {
  const { type, backend, vector, dim, elapsed, message } = e.data
  if (type === 'ready') {
    $('status').textContent = '状态：模型已就绪'
    $('backend').textContent = `推理后端：${backend}（${backend === 'webgpu' ? 'GPU' : 'CPU/WASM'}）`
    if (backend === 'wasm') $('backend').textContent += ' —— 已自动降级'
  } else if (type === 'result') {
    const len = vector.length
    $('result').innerHTML =
      `维度：${dim.join('×')}，耗时 ${elapsed} ms<br>` +
      `向量前 8 个值：${vector.slice(0, 8).map((v) => v.toFixed(4)).join(', ')}`
  } else if (type === 'error') {
    $('status').textContent = '状态：出错'
    $('result').textContent = '错误：' + message
  }
}

// 3) 触发加载
$('status').textContent = '状态：正在加载模型并初始化推理后端…'
worker.postMessage({ type: 'init' })

// 4) 用户点按钮 -> 本地生成向量
$('btn').addEventListener('click', () => {
  const text = $('text').value.trim()
  if (!text) return
  $('result').textContent = '正在计算…'
  worker.postMessage({ type: 'embed', payload: { text } })
})
