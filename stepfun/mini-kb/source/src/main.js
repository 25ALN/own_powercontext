import { detectGPU } from './backend.js'
import { BrowserEmbedding } from './embedding-client.js'
import { BATCH_SIZE } from '../shared/embedding.js'
import './style.css'

const $ = (id) => document.getElementById(id)
const state = { kbId: '', version: 0, ready: false, busy: false, searching: false }
const embedding = new BrowserEmbedding((message) => { $('modelStatus').textContent = message })

async function api(path, method = 'GET', data) {
  const response = await fetch(`/api${path}`, {
    method,
    ...(data !== undefined ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) } : {}),
  })
  const result = await response.json()
  if (!response.ok) throw new Error(result.error || `请求失败：${response.status}`)
  return result
}

function message(text, error = false) {
  $('message').textContent = text
  $('message').classList.toggle('error', error)
}

function controls() {
  $('importButton').disabled = !state.ready || !state.kbId || state.busy
  $('searchButton').disabled = !state.ready || !state.kbId || state.busy || state.searching
  $('kbSelect').disabled = state.busy
  $('createKb').disabled = state.busy
  $('deleteKb').disabled = !state.kbId || state.busy
  $('refreshIndex').disabled = !state.kbId || state.busy
  $('files').disabled = state.busy
}

async function loadKbs(selected = state.kbId) {
  const { kbs } = await api('/kbs')
  $('kbSelect').replaceChildren()
  for (const kb of kbs) {
    const option = document.createElement('option')
    option.value = kb.id
    option.textContent = `${kb.name}（${kb.documentCount} 篇）`
    $('kbSelect').append(option)
  }
  const next = kbs.find((kb) => kb.id === selected)?.id || kbs[0]?.id || ''
  $('kbSelect').value = next
  await selectKb(next)
}

async function selectKb(kbId) {
  state.kbId = kbId
  const version = ++state.version
  $('results').replaceChildren()
  $('searchStatus').textContent = ''
  $('documentList').replaceChildren()
  $('documentDialog').close()
  controls()
  if (!kbId) {
    $('documentList').textContent = '先创建一个知识库，再导入文档。'
    return
  }
  const { documents } = await api(`/kbs/${kbId}/documents`)
  if (version !== state.version) return
  renderDocuments(documents, kbId)
}

function button(text, onClick) {
  const element = document.createElement('button')
  element.type = 'button'
  element.textContent = text
  element.addEventListener('click', onClick)
  return element
}

async function showOriginal(kbId, documentId) {
  const version = state.version
  try {
    const document = await api(`/kbs/${kbId}/documents/${documentId}`)
    if (version !== state.version || kbId !== state.kbId) return
    $('documentTitle').textContent = document.name
    $('documentText').textContent = document.text
    $('documentDialog').showModal()
  } catch (error) { message(error.message, true) }
}

function renderDocuments(documents, kbId) {
  $('documentList').replaceChildren()
  if (!documents.length) $('documentList').textContent = '当前知识库还没有文档。'
  for (const doc of documents) {
    const row = document.createElement('li')
    const label = document.createElement('span')
    label.textContent = `${doc.name} · ${doc.chunkCount} 块 · ${doc.status === 'ready' ? '可检索' : '导入失败，请删除后重新导入'} · ${doc.backend}`
    row.append(label, button('原文', () => showOriginal(kbId, doc.id)), button('删除', async () => {
      if (state.busy || kbId !== state.kbId) return
      state.busy = true
      controls()
      try {
        await api(`/kbs/${kbId}/documents/${doc.id}`, 'DELETE')
        await loadKbs(kbId)
        message(`已删除 ${doc.name}`)
      } catch (error) { message(error.message, true) }
      finally { state.busy = false; controls() }
    }))
    $('documentList').append(row)
  }
}

$('kbSelect').addEventListener('change', () => {
  selectKb($('kbSelect').value).catch((error) => message(error.message, true))
})
$('kbForm').addEventListener('submit', async (event) => {
  event.preventDefault()
  if (state.busy) return
  state.busy = true
  controls()
  try {
    const kb = await api('/kbs', 'POST', { name: $('kbName').value })
    $('kbName').value = ''
    await loadKbs(kb.id)
    message('知识库已创建')
  } catch (error) { message(error.message, true) }
  finally { state.busy = false; controls() }
})
$('deleteKb').addEventListener('click', async () => {
  if (!state.kbId || state.busy) return
  if (!window.confirm('删除当前知识库及其中全部文档？')) return
  state.busy = true
  controls()
  try {
    await api(`/kbs/${state.kbId}`, 'DELETE')
    await loadKbs()
    message('知识库已删除')
  } catch (error) { message(error.message, true) }
  finally { state.busy = false; controls() }
})
$('refreshIndex').addEventListener('click', async () => {
  if (!state.kbId || state.busy) return
  state.busy = true
  controls()
  try {
    await api(`/kbs/${state.kbId}/refresh`, 'POST', {})
    message('索引刷新完成，可以检索最新文档')
  } catch (error) { message(error.message, true) }
  finally { state.busy = false; controls() }
})

$('importForm').addEventListener('submit', async (event) => {
  event.preventDefault()
  if (state.busy || !state.ready || !state.kbId) return
  const files = [...$('files').files]
  if (!files.length) return message('请选择 TXT 或 Markdown 文件', true)
  const kbId = state.kbId
  state.busy = true
  ++state.version
  $('results').replaceChildren()
  $('searchStatus').textContent = ''
  controls()
  let completed = 0
  try {
    for (const file of files) {
      if (file.size > 1024 * 1024) throw new Error(`${file.name} 超过 1 MiB`)
      const text = new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(await file.arrayBuffer())
      const { chunks } = await api('/chunks', 'POST', { name: file.name, text })
      const vectors = []
      for (let offset = 0; offset < chunks.length; offset += BATCH_SIZE) {
        message(`${file.name}：浏览器 ${embedding.backend} 向量化 ${offset}/${chunks.length} 块…`)
        vectors.push(...await embedding.embed(chunks.slice(offset, offset + BATCH_SIZE).map((chunk) => chunk.text)))
      }
      message(`${file.name}：向量化完成，正在写入 seekdb 并刷新索引…`)
      await api(`/kbs/${kbId}/documents`, 'POST', {
        name: file.name, text, vectors, embedding: embedding.embedding, backend: embedding.backend,
      })
      completed++
    }
    $('files').value = ''
    message(`已导入 ${completed} 篇文档，索引已刷新`)
  } catch (error) { message(`已完成 ${completed} 篇；${error.message}`, true) }
  finally {
    try { await loadKbs(kbId) }
    catch (error) { message(error.message, true) }
    state.busy = false
    controls()
  }
})

$('searchForm').addEventListener('submit', async (event) => {
  event.preventDefault()
  if (!state.ready || !state.kbId || state.busy || state.searching) return
  const text = $('query').value.trim()
  if (!text) return
  const kbId = state.kbId
  const version = state.version
  state.searching = true
  controls()
  $('results').replaceChildren()
  $('searchStatus').textContent = '正在浏览器本地生成查询向量…'
  const started = performance.now()
  try {
    const [vector] = await embedding.embed([text])
    const embeddingMs = performance.now() - started
    if (version !== state.version) return
    $('searchStatus').textContent = 'seekdb 正在执行全文 + 向量检索与 RRF 排序…'
    const { results, timings } = await api(`/kbs/${kbId}/search`, 'POST', {
      text, vector, topK: Number($('topK').value), embedding: embedding.embedding,
    })
    if (version !== state.version || kbId !== state.kbId) return
    $('searchStatus').textContent = `${results.length} 个结果 · seekdb RRF 排序 · 总计 ${(performance.now() - started).toFixed(0)} ms（本地向量 ${embeddingMs.toFixed(0)} ms，seekdb ${timings.searchMs.toFixed(0)} ms）`
    if (!results.length) $('results').textContent = '暂无结果，请先导入文档。'
    for (const [index, result] of results.entries()) {
      const article = document.createElement('article')
      const heading = document.createElement('h3')
      heading.textContent = `${index + 1}. ${result.metadata.name} · 分块 ${result.metadata.chunkIndex + 1}/${result.metadata.chunkCount}`
      const detail = document.createElement('div')
      detail.className = 'muted'
      detail.textContent = `RRF 分数 ${Number(result.score).toFixed(6)} · 文档 ${result.metadata.documentId}`
      const content = document.createElement('pre')
      content.textContent = result.text
      article.append(heading, detail, content, button('查看文档原文', () => showOriginal(kbId, result.metadata.documentId)))
      $('results').append(article)
    }
  } catch (error) {
    if (version === state.version) $('searchStatus').textContent = `检索失败：${error.message}`
  } finally { state.searching = false; controls() }
})
$('closeDocument').addEventListener('click', () => $('documentDialog').close())

$('gpuHint').textContent = detectGPU()
  ? '检测到 WebGPU API，优先尝试 GPU；实际后端以初始化结果为准。'
  : '未检测到 WebGPU API，将使用 WASM（CPU）。'
controls()
Promise.all([api('/health'), loadKbs()]).then(([health]) => {
  $('databaseStatus').textContent = `数据库已连接 · ${health.version}`
}).catch((error) => { $('databaseStatus').textContent = `数据库连接失败：${error.message}` })
embedding.init().then(({ backend, embedding: spec, readyMs, fallbackReason }) => {
  state.ready = true
  $('modelStatus').textContent = `模型已就绪 · ${spec.model} · ${spec.dtype} · ${spec.dimension} 维 · 加载及预热 ${(readyMs / 1000).toFixed(2)} s`
  $('backend').textContent = `实际推理后端：${backend === 'webgpu' ? 'WebGPU（已完成模型推理校验）' : 'WASM（CPU）'}${fallbackReason ? `；WebGPU 降级原因：${fallbackReason}` : ''}`
  controls()
}).catch((error) => {
  $('modelStatus').textContent = `模型加载失败：${error.message}。检查模型下载网络后刷新页面重试。`
})
