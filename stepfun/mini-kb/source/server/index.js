import { createServer } from 'node:http'
import { readFile, stat } from 'node:fs/promises'
import { dirname, extname, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { KnowledgeStore } from './store.js'
import { EMBEDDING } from '../shared/embedding.js'
import { MAX_FILE_BYTES, MAX_CHUNKS, prepareDocument } from './chunks.js'
import { check, HttpError } from './validation.js'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
try { process.loadEnvFile(resolve(root, '.env')) }
catch (error) { if (error.code !== 'ENOENT') throw error }
const dev = process.argv.includes('--dev')
const store = new KnowledgeStore()
try { await store.open() }
catch (error) {
  console.error('seekdb 初始化失败。请先启动本地数据库并检查 .env：', error.message)
  await store.close()
  process.exit(1)
}

function json(res, status, data) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' })
  res.end(JSON.stringify(data))
}

async function body(req) {
  let bytes = 0
  const buffers = []
  for await (const chunk of req) {
    bytes += chunk.length
    check(bytes <= 12 * 1024 * 1024, '请求不能超过 12 MiB', 413)
    buffers.push(chunk)
  }
  let parsed
  try { parsed = JSON.parse(Buffer.concat(buffers).toString('utf8')) }
  catch { throw new HttpError(400, '请求体必须为 JSON') }
  check(parsed && typeof parsed === 'object' && !Array.isArray(parsed), '请求体必须为 JSON 对象')
  return parsed
}

async function api(req, res, path) {
  const method = req.method
  const data = method === 'POST' ? await body(req) : undefined
  const result = await store.run(async () => {
    if (method === 'GET' && path === '/api/health') {
      await store.client.execute('SELECT 1')
      return {
        database: 'connected', version: store.version, embedding: EMBEDDING,
        maxFileBytes: MAX_FILE_BYTES, maxChunks: MAX_CHUNKS,
      }
    }
    if (method === 'POST' && path === '/api/chunks') return { chunks: prepareDocument(data).chunks, embedding: EMBEDDING }
    if (method === 'GET' && path === '/api/kbs') return { kbs: await store.listKbs() }
    if (method === 'POST' && path === '/api/kbs') return store.createKb(data?.name)
    const match = path.match(/^\/api\/kbs\/([a-f0-9-]{36})(?:\/(documents|search|refresh)(?:\/([a-f0-9-]{36}))?)?$/)
    check(match, '接口不存在', 404)
    const [, kbId, action, documentId] = match
    if (method === 'DELETE' && !action) { await store.deleteKb(kbId); return { deleted: true } }
    if (action === 'documents') {
      if (method === 'GET') return documentId ? store.document(kbId, documentId) : { documents: await store.listDocuments(kbId) }
      if (method === 'POST' && !documentId) return store.importDocument(kbId, data)
      if (method === 'DELETE' && documentId) { await store.deleteDocument(kbId, documentId); return { deleted: true } }
    }
    if (!documentId && method === 'POST' && action === 'search') return store.search(kbId, data)
    if (!documentId && method === 'POST' && action === 'refresh') return store.refresh(kbId)
    throw new HttpError(404, '接口不存在')
  })
  json(res, 200, result)
}

const mime = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.mjs': 'text/javascript',
  '.css': 'text/css', '.json': 'application/json', '.wasm': 'application/wasm', '.svg': 'image/svg+xml',
}
let vite
const server = createServer(async (req, res) => {
  try {
    const path = decodeURIComponent(new URL(req.url, 'http://localhost').pathname)
    if (path === '/api' || path.startsWith('/api/')) return await api(req, res, path)
    // ORT dynamically imports public .mjs files. Vite appends ?import and then
    // skips its public-file handler; serve these runtime assets without transforms.
    if (dev && path.startsWith('/ort/')) {
      const publicDir = resolve(root, 'public', 'ort')
      const asset = resolve(publicDir, path.slice('/ort/'.length))
      check(asset.startsWith(publicDir + sep), 'Forbidden', 403)
      const bytes = await readFile(asset)
      res.writeHead(200, {
        'Content-Type': mime[extname(asset)] || 'application/octet-stream', 'Content-Length': bytes.length,
      })
      return res.end(bytes)
    }
    if (vite) return vite.middlewares(req, res)
    check(req.method === 'GET' || req.method === 'HEAD', 'Method not allowed', 405)
    const dist = resolve(root, 'dist')
    const file = resolve(dist, path === '/' ? 'index.html' : `.${path}`)
    check(file.startsWith(dist + sep), 'Forbidden', 403)
    try {
      check((await stat(file)).isFile(), '文件不存在', 404)
      const bytes = await readFile(file)
      res.writeHead(200, { 'Content-Type': mime[extname(file)] || 'application/octet-stream', 'Content-Length': bytes.length })
      res.end(req.method === 'HEAD' ? undefined : bytes)
    } catch (error) {
      if (error.code === 'ENOENT') throw new HttpError(404, '文件不存在，请先运行 npm run build')
      throw error
    }
  } catch (error) {
    if (res.headersSent) return res.end()
    if (!error.status) console.error(error)
    json(res, error.status || 500, { error: error.message || '服务器错误' })
  }
})
if (dev) {
  const { createServer: createVite } = await import('vite')
  vite = await createVite({ root, server: { middlewareMode: true, hmr: { server } } })
}
const port = Number(process.env.PORT || (dev ? 5173 : 3001))
const host = process.env.HOST || '127.0.0.1'
server.listen(port, host, () => console.log(`mini-kb: http://${host}:${port} · ${store.version}`))
async function shutdown() {
  server.close()
  await vite?.close()
  await store.close()
  process.exit(0)
}
process.once('SIGINT', shutdown)
process.once('SIGTERM', shutdown)
