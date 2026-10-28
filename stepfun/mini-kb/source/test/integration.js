// Run against the real HTTP server and seekdb. These fixed unit vectors test
// storage/ranking/isolation, and never invoke a Node-side embedding model.
import assert from 'node:assert/strict'
import { EMBEDDING } from '../shared/embedding.js'
const origin = process.env.TEST_ORIGIN || 'http://127.0.0.1:5173'
const ids = []
const unit = (axis) => Array.from({ length: 384 }, (_, index) => index === axis ? 1 : 0)

async function request(path, method = 'GET', payload, status = 200) {
  const response = await fetch(`${origin}/api${path}`, {
    method, ...(payload !== undefined ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) } : {}),
  })
  const result = await response.json()
  assert.equal(response.status, status, JSON.stringify(result))
  return result
}
async function add(kbId, name, text, axis) {
  const { chunks } = await request('/chunks', 'POST', { name, text })
  return request(`/kbs/${kbId}/documents`, 'POST', {
    name, text, vectors: chunks.map(() => unit(axis)), embedding: EMBEDDING, backend: 'wasm',
  })
}
const query = (text, axis = 0, topK = 5) => ({ text, vector: unit(axis), topK, embedding: EMBEDDING })

try {
  const health = await request('/health')
  assert.equal(health.embedding.dimension, 384)
  for (const suffix of ['A', 'B', 'empty']) {
    const kb = await request('/kbs', 'POST', { name: `integration-${Date.now()}-${suffix}` })
    ids.push(kb.id)
  }
  const [a, b, empty] = ids
  const original = '# OceanBase\r\n数据库混合检索支持向量检索和全文检索。\n' + '索引刷新后立即可见。\n'.repeat(80)
  const docA = await add(a, 'database.md', original, 0)
  assert.ok(docA.chunkCount > 1)
  await add(a, 'cats.txt', 'Cats sleep in sunny windows. Felines love naps.', 1)
  const docB = await add(b, 'secret.txt', '数据库混合检索。B_ONLY_SECRET', 0)
  assert.equal((await request(`/kbs/${a}/documents/${docA.id}`)).text, original)
  await request(`/kbs/${b}/documents/${docA.id}`, 'GET', undefined, 404)
  await request(`/kbs/${b}/documents/${docA.id}`, 'DELETE', undefined, 404)

  const found = await request(`/kbs/${a}/search`, 'POST', query('数据库混合检索'))
  assert.ok(found.results.length > 0)
  assert.equal(found.results[0].metadata.documentId, docA.id)
  assert.ok(found.results[0].score > 0)
  assert.ok(found.results.every((item) => item.metadata.kbId === a && !item.text.includes('B_ONLY_SECRET')))
  // The keyword-only path must contribute: its vector is orthogonal to the query,
  // while the other document has perfect vector similarity and no keyword match.
  const keyword = await add(empty, 'keyword.txt', '斑马数据库 zebraquokka', 1)
  await add(empty, 'vector.txt', 'A different semantic candidate.', 0)
  const fused = await request(`/kbs/${empty}/search`, 'POST', query('斑马数据库 zebraquokka'))
  assert.ok(fused.results.some((item) => item.metadata.documentId === keyword.id))
  const keywordResult = fused.results.find((item) => item.metadata.documentId === keyword.id)
  const vectorResult = fused.results.find((item) => item.metadata.name === 'vector.txt')
  assert.ok(keywordResult.score > vectorResult.score, '全文命中须对数据库内 RRF 排序产生贡献')

  const late = await add(a, 'new.txt', 'Fresh refresh token NEWLY_INDEXED，新增文档可以检索。', 2)
  const immediate = await request(`/kbs/${a}/search`, 'POST', query('NEWLY_INDEXED', 2, 1))
  assert.equal(immediate.results.length, 1)
  assert.equal(immediate.results[0].metadata.documentId, late.id)
  await request(`/kbs/${a}/refresh`, 'POST', {})
  const concurrent = await Promise.all(Array.from({ length: 12 }, (_, index) => {
    const target = index % 2 ? a : b
    return request(`/kbs/${target}/search`, 'POST', query('数据库')).then((result) => ({ target, result }))
  }))
  for (const { target, result } of concurrent) {
    assert.ok(result.results.length)
    assert.ok(result.results.every((item) => item.metadata.kbId === target))
  }
  await request(`/kbs/${a}/search`, 'POST', { ...query('x'), vector: [1] }, 400)
  await request(`/kbs/${a}/search`, 'POST', { ...query('x'), embedding: { ...EMBEDDING, dimension: 768 } }, 400)
  await request(`/kbs/${a}/documents`, 'POST', { name: 'bad.txt', text: 'x', vectors: [] }, 400)
  await request('/chunks', 'POST', { name: 'bad.pdf', text: 'x' }, 400)
  await request(`/kbs/${b}/documents/${docB.id}`, 'DELETE')
  assert.deepEqual((await request(`/kbs/${b}/search`, 'POST', query('数据库'))).results, [])
  console.log('PASS: 真实 seekdb 写入、原文回读、数据库 RRF 全文贡献、索引刷新后可见、Top K、并发知识库隔离、删除、参数校验')
} finally {
  for (const id of ids) await request(`/kbs/${id}`, 'DELETE')
}
