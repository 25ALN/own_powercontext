import { randomUUID } from 'node:crypto'
import { SeekdbAdminClient, SeekdbClient, Schema, FulltextIndexConfig, VectorIndexConfig } from 'seekdb'
import { EMBEDDING } from '../shared/embedding.js'
import { prepareDocument } from './chunks.js'
import { check, validateEmbedding, validateVector } from './validation.js'

export class KnowledgeStore {
  constructor() {
    this.collections = new Map()
    this.queue = Promise.resolve()
  }

  // seekdb-js uses a single SQL session and @search_parm for hybridSearch.
  // Serialize complete operations so concurrent requests cannot replace its DSL
  // or interleave document publication with a search/delete.
  run(operation) {
    const next = this.queue.then(operation)
    this.queue = next.catch(() => {})
    return next
  }

  async open() {
    const database = process.env.SEEKDB_DATABASE || 'mini_kb'
    check(/^[a-zA-Z_][a-zA-Z0-9_]{0,63}$/.test(database), '无效的 SEEKDB_DATABASE')
    const connection = {
      host: process.env.SEEKDB_HOST || '127.0.0.1',
      port: Number(process.env.SEEKDB_PORT || 2881),
      user: process.env.SEEKDB_USER || 'root',
      password: process.env.SEEKDB_PASSWORD || '',
      ...(process.env.SEEKDB_TENANT ? { tenant: process.env.SEEKDB_TENANT } : {}),
    }
    const admin = new SeekdbAdminClient(connection)
    try {
      const databases = await admin.listDatabases()
      if (!databases.some((entry) => entry.name === database)) await admin.createDatabase(database)
    } finally {
      await admin.close()
    }
    this.client = new SeekdbClient({ ...connection, database, queryTimeout: 60000 })
    this.version = (await this.client.execute('SELECT VERSION() AS version'))[0].version
    await this.client.execute(`CREATE TABLE IF NOT EXISTS mini_kb_spaces (
      id VARCHAR(36) PRIMARY KEY, name VARCHAR(100) NOT NULL,
      collection_name VARCHAR(64) NOT NULL, created_at VARCHAR(30) NOT NULL
    )`)
    await this.client.execute(`CREATE TABLE IF NOT EXISTS mini_kb_documents (
      id VARCHAR(36) PRIMARY KEY, kb_id VARCHAR(36) NOT NULL,
      name VARCHAR(255) NOT NULL, original_text LONGTEXT NOT NULL,
      chunk_count INT NOT NULL, status VARCHAR(16) NOT NULL,
      embedding_backend VARCHAR(16) NOT NULL, created_at VARCHAR(30) NOT NULL,
      INDEX idx_kb (kb_id)
    )`)
    // Interrupted uploads remain hidden from both retrieval paths.
    await this.client.execute("UPDATE mini_kb_documents SET status = 'failed' WHERE status = 'importing'")
  }

  async listKbs() {
    return this.client.execute(`SELECT k.id, k.name, k.created_at AS createdAt,
      (SELECT COUNT(*) FROM mini_kb_documents d WHERE d.kb_id=k.id AND d.status='ready') AS documentCount
      FROM mini_kb_spaces k ORDER BY k.created_at, k.id`)
  }

  async kb(id) {
    const rows = await this.client.execute('SELECT * FROM mini_kb_spaces WHERE id = ?', [id])
    check(rows.length, '知识库不存在', 404)
    return rows[0]
  }

  async collection(kb) {
    if (!this.collections.has(kb.id)) {
      const collection = await this.client.getCollection({ name: kb.collection_name, embeddingFunction: null })
      check(collection.dimension === EMBEDDING.dimension && collection.distance === 'cosine',
        '知识库向量配置与浏览器模型不一致', 409)
      this.collections.set(kb.id, collection)
    }
    return this.collections.get(kb.id)
  }

  async createKb(name) {
    check(typeof name === 'string' && name.trim().length > 0 && name.trim().length <= 100,
      '知识库名称须为 1–100 个字符')
    const id = randomUUID()
    const collectionName = `kb_${id.replaceAll('-', '')}`
    const collection = await this.client.createCollection({
      name: collectionName,
      schema: new Schema({
        fulltextIndex: new FulltextIndexConfig('ik'),
        vectorIndex: new VectorIndexConfig({
          hnsw: { dimension: EMBEDDING.dimension, distance: 'cosine' },
          embeddingFunction: null,
        }),
      }),
      embeddingFunction: null,
    })
    try {
      await this.client.execute('INSERT INTO mini_kb_spaces VALUES (?, ?, ?, ?)',
        [id, name.trim(), collectionName, new Date().toISOString()])
      this.collections.set(id, collection)
    } catch (error) {
      await this.client.deleteCollection(collectionName)
      throw error
    }
    return { id, name: name.trim() }
  }

  async deleteKb(id) {
    const kb = await this.kb(id)
    await this.client.deleteCollection(kb.collection_name)
    await this.client.execute('DELETE FROM mini_kb_documents WHERE kb_id = ?', [id])
    await this.client.execute('DELETE FROM mini_kb_spaces WHERE id = ?', [id])
    this.collections.delete(id)
  }

  async listDocuments(kbId) {
    await this.kb(kbId)
    return this.client.execute(`SELECT id, name, chunk_count AS chunkCount, status,
      embedding_backend AS backend, created_at AS createdAt
      FROM mini_kb_documents WHERE kb_id = ? ORDER BY created_at DESC, id`, [kbId])
  }

  async document(kbId, id) {
    await this.kb(kbId)
    const rows = await this.client.execute(`SELECT id, name, original_text AS text,
      chunk_count AS chunkCount, status FROM mini_kb_documents WHERE kb_id = ? AND id = ?`, [kbId, id])
    check(rows.length, '文档不存在于当前知识库', 404)
    return rows[0]
  }

  async importDocument(kbId, payload) {
    const kb = await this.kb(kbId)
    const { name, text, chunks } = prepareDocument(payload)
    validateEmbedding(payload.embedding)
    check(['wasm', 'webgpu'].includes(payload.backend), '缺少浏览器实际推理后端信息')
    check(Array.isArray(payload.vectors) && payload.vectors.length === chunks.length,
      '向量数量与服务端分块数量不一致')
    payload.vectors.forEach(validateVector)
    const collection = await this.collection(kb)
    const id = randomUUID()
    const createdAt = new Date().toISOString()
    const writeStarted = performance.now()
    const timings = {}
    await this.client.execute('INSERT INTO mini_kb_documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
      [id, kbId, name, text, chunks.length, 'importing', payload.backend, createdAt])
    try {
      for (let offset = 0; offset < chunks.length; offset += 32) {
        const batch = chunks.slice(offset, offset + 32)
        await collection.add({
          ids: batch.map((chunk) => `${id}:${chunk.index}`),
          documents: batch.map((chunk) => chunk.text),
          embeddings: payload.vectors.slice(offset, offset + batch.length),
          metadatas: batch.map((chunk) => ({
            kbId, documentId: id, name, chunkIndex: chunk.index, chunkCount: chunks.length,
            start: chunk.start, end: chunk.end, model: EMBEDDING.model,
            dimension: EMBEDDING.dimension, backend: payload.backend, createdAt,
            dtype: payload.embedding.dtype, revision: EMBEDDING.revision,
          })),
        })
      }
      timings.writeMs = performance.now() - writeStarted
      const refreshStarted = performance.now()
      await collection.refresh_index()
      timings.refreshMs = performance.now() - refreshStarted
      await this.client.execute("UPDATE mini_kb_documents SET status = 'ready' WHERE kb_id = ? AND id = ?", [kbId, id])
      timings.totalMs = performance.now() - writeStarted
    } catch (error) {
      // Searches only admit document IDs in ready state, including after crashes.
      await this.client.execute("UPDATE mini_kb_documents SET status = 'failed' WHERE kb_id = ? AND id = ?", [kbId, id])
      try { await collection.delete({ where: { documentId: { $eq: id } } }) }
      catch (cleanupError) { console.error('清理未完成文档失败：', cleanupError.message) }
      throw error
    }
    return { id, kbId, name, chunkCount: chunks.length, status: 'ready', backend: payload.backend, timings }
  }

  async deleteDocument(kbId, id) {
    const kb = await this.kb(kbId)
    await this.document(kbId, id)
    // Unpublish before deleting vectors. Even a failed delete cannot be searched.
    await this.client.execute("UPDATE mini_kb_documents SET status = 'failed' WHERE kb_id = ? AND id = ?", [kbId, id])
    const collection = await this.collection(kb)
    await collection.delete({ where: { documentId: { $eq: id } } })
    await collection.refresh_index()
    await this.client.execute('DELETE FROM mini_kb_documents WHERE kb_id = ? AND id = ?', [kbId, id])
  }

  async refresh(kbId) {
    const collection = await this.collection(await this.kb(kbId))
    await collection.refresh_index()
    return { refreshed: true }
  }

  async search(kbId, { text, vector, embedding, topK = 5 } = {}) {
    const started = performance.now()
    const kb = await this.kb(kbId)
    check(typeof text === 'string' && text.trim().length > 0 && text.length <= 1000,
      '查询须为 1–1000 个字符')
    check(Number.isInteger(topK) && topK >= 1 && topK <= 50, 'Top K 须为 1–50 的整数')
    validateEmbedding(embedding)
    validateVector(vector)
    const documents = await this.client.execute("SELECT id FROM mini_kb_documents WHERE kb_id = ? AND status = 'ready'", [kbId])
    if (!documents.length) return { kbId, results: [], ranking: 'seekdb RRF', timings: { searchMs: 0, totalMs: performance.now() - started } }
    const collection = await this.collection(kb)
    const where = { $and: [{ kbId: { $eq: kbId } }, { documentId: { $in: documents.map((doc) => doc.id) } }] }
    const candidates = Math.max(20, topK * 4)
    const searchStarted = performance.now()
    const result = await collection.hybridSearch({
      query: { whereDocument: { $contains: text.trim() }, where, nResults: candidates },
      knn: { queryEmbeddings: [vector], where, nResults: candidates },
      rank: { rrf: { rankConstant: 60, rankWindowSize: candidates } },
      nResults: topK,
      include: ['documents', 'metadatas', 'distances'],
    })
    // Preserve the database's ordering and fused score; no application-side fusion.
    return {
      kbId, ranking: 'seekdb RRF',
      timings: { searchMs: performance.now() - searchStarted, totalMs: performance.now() - started },
      results: result.ids[0].map((id, index) => ({
        id, text: result.documents[0][index], metadata: result.metadatas[0][index],
        score: result.distances[0][index],
      })),
    }
  }

  async close() {
    await this.queue
    if (this.client) await this.client.close()
  }
}
