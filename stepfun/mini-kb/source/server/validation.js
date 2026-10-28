import { EMBEDDING } from '../shared/embedding.js'

export class HttpError extends Error {
  constructor(status, message) {
    super(message)
    this.status = status
  }
}

export function check(condition, message, status = 400) {
  if (!condition) throw new HttpError(status, message)
}

export function validateEmbedding(spec) {
  check(spec && Object.entries(EMBEDDING).every(([key, value]) => spec[key] === value),
    'Embedding 配置不匹配，请刷新页面并使用当前浏览器模型重新生成向量')
}

export function validateVector(vector) {
  check(Array.isArray(vector) && vector.length === EMBEDDING.dimension
    && vector.every((value) => typeof value === 'number' && Number.isFinite(value)),
  `向量必须包含 ${EMBEDDING.dimension} 个有限数值`)
  const norm = Math.sqrt(vector.reduce((sum, value) => sum + value * value, 0))
  check(Math.abs(norm - 1) < 0.02, '需要浏览器生成的 L2 归一化向量')
}
