import test from 'node:test'
import assert from 'node:assert/strict'
import { splitText, prepareDocument, CHUNK_SIZE } from '../server/chunks.js'
import { validateVector, validateEmbedding } from '../server/validation.js'
import { EMBEDDING } from '../shared/embedding.js'

test('分块保留原文位置、覆盖长文本且不拆开代理对', () => {
  const text = '\uFEFF# 标题\r\n' + 'OceanBase 数据库检索。🙂\n'.repeat(150)
  const chunks = splitText(text)
  assert.ok(chunks.length > 2)
  let covered = 0
  for (const [index, chunk] of chunks.entries()) {
    assert.equal(chunk.index, index)
    assert.equal(chunk.text, text.slice(chunk.start, chunk.end))
    assert.ok(chunk.start <= covered)
    assert.ok(chunk.end > covered)
    assert.ok(chunk.text.length <= CHUNK_SIZE)
    assert.ok(!/^[\uDC00-\uDFFF]/.test(chunk.text))
    assert.ok(!/[\uD800-\uDBFF]$/.test(chunk.text))
    covered = chunk.end
  }
  assert.equal(covered, text.length)
})

test('拒绝空文档、错误格式及过多分块', () => {
  for (const payload of [
    { name: 'x.txt', text: '   ' }, { name: 'x.pdf', text: 'x' },
    { name: 'x.md', text: 'a'.repeat(210000) }, { name: 'x.txt', text: '\uFFFD' },
  ]) assert.throws(() => prepareDocument(payload))
  assert.equal(prepareDocument({ name: '文档.MD', text: '# hello\nworld' }).chunks.length, 1)
})

test('拒绝错误模型、非 384 维、非有限值及非归一化向量', () => {
  validateEmbedding(EMBEDDING)
  validateVector([1, ...Array(383).fill(0)])
  assert.throws(() => validateEmbedding({ ...EMBEDDING, model: 'other' }))
  for (const vector of [[], Array(384).fill(0), Array(384).fill(NaN), Array(384).fill('1')]) {
    assert.throws(() => validateVector(vector))
  }
})
