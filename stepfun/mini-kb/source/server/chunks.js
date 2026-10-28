import { check } from './validation.js'

// Keep Chinese-heavy chunks within MiniLM's 256-token training sequence length.
export const CHUNK_SIZE = 240
export const CHUNK_OVERLAP = 40
export const MAX_CHUNKS = 512
export const MAX_FILE_BYTES = 1024 * 1024

// Offsets refer to UTF-16 positions in the unmodified original text.
// Prefer paragraph/sentence boundaries, but always advance and retain overlap.
export function splitText(text) {
  const chunks = []
  let start = 0
  while (start < text.length) {
    let end = Math.min(start + CHUNK_SIZE, text.length)
    if (end < text.length) {
      const region = text.slice(start, end)
      const breaks = [...region.matchAll(/\n|[。！？.!?]\s/g)]
      const boundary = breaks.at(-1)
      if (boundary && boundary.index >= CHUNK_SIZE / 2) end = start + boundary.index + boundary[0].length
      if (/[\uD800-\uDBFF]/.test(text[end - 1])) end--
    }
    if (text.slice(start, end).trim()) {
      chunks.push({ index: chunks.length, start, end, text: text.slice(start, end) })
    }
    if (end === text.length) break
    start = Math.max(start + 1, end - CHUNK_OVERLAP)
    if (/[\uDC00-\uDFFF]/.test(text[start])) start++
  }
  return chunks
}

export function prepareDocument({ name, text } = {}) {
  check(typeof name === 'string' && name.length <= 255 && /\.(txt|md|markdown)$/i.test(name),
    '请选择 TXT 或 Markdown 文档（文件名最长 255 字符）')
  check(typeof text === 'string' && text.trim().length > 0, '文档内容不能为空')
  check(Buffer.byteLength(text, 'utf8') <= MAX_FILE_BYTES, '单个文档不能超过 1 MiB', 413)
  check(!text.includes('\0') && !text.includes('\uFFFD'), '请使用 UTF-8 编码的纯文本文件')
  const chunks = splitText(text)
  check(chunks.length > 0 && chunks.length <= MAX_CHUNKS, `单个文档最多 ${MAX_CHUNKS} 个分块`, 413)
  return { name, text, chunks }
}
