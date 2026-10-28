// 主线程判断浏览器是否暴露 WebGPU API（注意：有 API 不代表一定初始化成功，
// 真正的成败以 worker 里 pipeline 初始化结果为准）
export function detectGPU() {
  return typeof navigator !== 'undefined' && 'gpu' in navigator
}
