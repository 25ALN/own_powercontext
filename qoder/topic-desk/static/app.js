"use strict";

const $ = (id) => document.getElementById(id);
let currentTopic = null;

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  let body = null;
  try { body = await response.json(); } catch { /* non-JSON */ }
  if (!response.ok) {
    const message = body?.error?.message || `请求失败（HTTP ${response.status}）`;
    const err = new Error(message);
    err.code = body?.error?.code || "unknown";
    throw err;
  }
  return body;
}

function setPcStatus(reachable, url) {
  const el = $("pc-status");
  el.classList.remove("checking", "ok", "down");
  if (reachable) {
    el.classList.add("ok");
    $("pc-status-text").textContent = `PowerContext 已连接（${url}）`;
  } else {
    el.classList.add("down");
    $("pc-status-text").textContent = "PowerContext 不可用：请先启动 powercontext server run";
  }
}

function showGlobalError(message) {
  $("global-error").classList.remove("hidden");
  $("global-error").textContent = message;
}
function clearGlobalError() {
  $("global-error").classList.add("hidden");
  $("global-error").textContent = "";
}

function flash(btn, text) {
  const original = btn.textContent;
  btn.textContent = text;
  btn.disabled = true;
  setTimeout(() => { btn.textContent = original; btn.disabled = false; }, 1500);
}

async function checkHealth() {
  try {
    const health = await api("/api/health");
    setPcStatus(health.powercontext_reachable, health.powercontext_url);
    if (!health.powercontext_reachable) {
      showGlobalError("无法连接 PowerContext Server。请按运行说明启动：uv run powercontext server run");
    } else {
      clearGlobalError();
    }
    return health.powercontext_reachable;
  } catch (e) {
    setPcStatus(false, "");
    showGlobalError(e.message);
    return false;
  }
}

async function loadTopics() {
  const list = $("topic-list");
  list.innerHTML = '<div class="loading">正在从 PowerContext 读取课题…</div>';
  try {
    const data = await api("/api/topics");
    if (!data.topics.length) {
      list.innerHTML = `<div class="empty">还没有课题。点击上方「载入示例课题」快速体验，或展开「＋ 新建课题」创建第一个课题。</div>`;
      return;
    }
    list.innerHTML = "";
    for (const t of data.topics) {
      const card = document.createElement("div");
      card.className = "topic-card";
      card.innerHTML = `
        ${t.is_demo ? '<span class="badge">示例数据</span>' : ""}
        <h3></h3><p></p>`;
      card.querySelector("h3").textContent = t.title;
      card.querySelector("p").textContent = t.summary;
      card.addEventListener("click", () => openTopic(t.scope_id));
      list.appendChild(card);
    }
  } catch (e) {
    list.innerHTML = "";
    showGlobalError(e.message);
  }
}

async function openTopic(scopeId) {
  currentTopic = scopeId;
  try {
    const data = await api(`/api/topics/${encodeURIComponent(scopeId)}`);
    $("topic-title").textContent = data.topic.title;
    $("topic-summary").textContent = data.topic.summary;
    $("topic-scope").textContent = data.topic.scope_id;
    renderNotes(data.notes);
    $("view-topics").classList.add("hidden");
    $("view-topic").classList.remove("hidden");
    clearGlobalError();
    loadContext();
  } catch (e) {
    showGlobalError(e.message);
  }
}

function renderNotes(notes) {
  const list = $("note-list");
  if (!notes.length) {
    list.innerHTML = '<div class="empty">还没有记录。用上方表单写下第一条结论、事实、问题或进展。</div>';
    return;
  }
  list.innerHTML = "";
  for (const n of notes) {
    const div = document.createElement("div");
    div.className = "note";
    const meta = document.createElement("div");
    meta.className = "meta";
    const tag = document.createElement("span");
    tag.className = "kind-tag" + (n.kind === "handoff" ? " handoff" : "");
    tag.textContent = n.kind_label;
    meta.appendChild(tag);
    meta.appendChild(document.createTextNode(`版本 ${n.version} · ${n.entry_id?.slice(0, 8) ?? ""}`));
    const text = document.createElement("p");
    text.className = "text";
    text.textContent = n.text;
    div.appendChild(meta);
    div.appendChild(text);
    if (n.source) {
      const src = document.createElement("div");
      src.className = "src";
      const link = n.source.url
        ? `<a href="${n.source.url}" target="_blank" rel="noopener"></a>`
        : `<span></span>`;
      src.innerHTML = `来源：${link} <span class="sid"></span>`;
      const a = src.querySelector("a, span");
      a.textContent = n.source.title;
      src.querySelector(".sid").textContent = n.source.source_id ? `（source: ${n.source.source_id}）` : "";
      div.appendChild(src);
    }
    list.appendChild(div);
  }
}

async function loadContext() {
  const out = $("context-out");
  out.classList.remove("hidden");
  out.textContent = "正在从 PowerContext 准备续研包…";
  try {
    const data = await api(`/api/topics/${encodeURIComponent(currentTopic)}/context`);
    if (data.status === "empty") {
      out.textContent = "（PowerContext 返回空：该课题还没有可用于续研的历史上下文）";
    } else {
      out.textContent = data.content || "(无内容)";
    }
  } catch (e) {
    out.textContent = `读取失败：${e.message}`;
  }
}

async function loadDemo() {
  const btn = $("btn-seed");
  btn.disabled = true;
  btn.textContent = "正在写入示例数据…";
  try {
    const data = await api("/api/demo/seed", { method: "POST" });
    await checkHealth();
    await loadTopics();
    flash(btn, data.created ? "示例课题已写入 PowerContext" : "示例课题已存在");
  } catch (e) {
    showGlobalError(e.message);
    btn.disabled = false;
    btn.textContent = "载入示例课题";
  }
}

function wireForms() {
  $("form-create").addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const data = await api("/api/topics", {
        method: "POST",
        body: JSON.stringify({
          title: $("create-title").value.trim(),
          summary: $("create-summary").value.trim(),
        }),
      });
      $("create-title").value = "";
      $("create-summary").value = "";
      await loadTopics();
      openTopic(data.scope_id);
    } catch (e) { showGlobalError(e.message); }
  });

  $("form-note").addEventListener("submit", async (event) => {
    event.preventDefault();
    const btn = event.submitter || $("form-note").querySelector("button[type=submit]");
    btn.disabled = true;
    try {
      const excerpt = $("source-excerpt").value.trim();
      const title = $("source-title").value.trim();
      const url = $("source-url").value.trim();
      const body = {
        kind: $("note-kind").value,
        text: $("note-text").value.trim(),
        source: excerpt && title ? { title, url, excerpt } : null,
      };
      await api(`/api/topics/${encodeURIComponent(currentTopic)}/notes`, {
        method: "POST", body: JSON.stringify(body),
      });
      $("note-text").value = "";
      $("source-title").value = ""; $("source-url").value = ""; $("source-excerpt").value = "";
      const data = await api(`/api/topics/${encodeURIComponent(currentTopic)}`);
      renderNotes(data.notes);
      flash(btn, "已保存到 PowerContext");
    } catch (e) {
      showGlobalError(e.message);
    } finally {
      btn.disabled = false;
    }
  });

  $("form-handoff").addEventListener("submit", async (event) => {
    event.preventDefault();
    const btn = event.submitter || $("form-handoff").querySelector("button[type=submit]");
    btn.disabled = true;
    const lines = (id) => $(id).value.split("\n").map((s) => s.trim()).filter(Boolean);
    try {
      await api(`/api/topics/${encodeURIComponent(currentTopic)}/handoff`, {
        method: "POST",
        body: JSON.stringify({
          goal: $("ho-goal").value.trim(),
          done: lines("ho-done"),
          evidence: lines("ho-evidence"),
          open_questions: lines("ho-questions"),
          next_steps: lines("ho-next"),
        }),
      });
      const data = await api(`/api/topics/${encodeURIComponent(currentTopic)}`);
      renderNotes(data.notes);
      loadContext();
      flash(btn, "交接状态已保存");
    } catch (e) {
      showGlobalError(e.message);
    } finally {
      btn.disabled = false;
    }
  });

  $("form-search").addEventListener("submit", async (event) => {
    event.preventDefault();
    const out = $("search-out");
    out.innerHTML = '<div class="loading">搜索中…</div>';
    try {
      const q = $("search-q").value.trim();
      const data = await api(`/api/topics/${encodeURIComponent(currentTopic)}/search?q=${encodeURIComponent(q)}`);
      if (!data.hits.length) {
        out.innerHTML = `<div class="empty">没有匹配「${q}」的记录。</div>`;
        return;
      }
      out.innerHTML = "";
      for (const hit of data.hits) {
        const div = document.createElement("div");
        div.className = "search-hit";
        const p = document.createElement("div");
        let text = hit.text || "";
        const markerStart = text.indexOf("[[source:");
        if (markerStart !== -1) text = text.slice(0, markerStart).trimEnd();
        p.textContent = text;
        const score = document.createElement("div");
        score.className = "score";
        score.textContent = `相关度 ${hit.score?.toFixed(3) ?? "-"} · entry ${hit.entry_id?.slice(0, 12) ?? ""}`;
        div.appendChild(p); div.appendChild(score);
        out.appendChild(div);
      }
    } catch (e) {
      out.innerHTML = "";
      showGlobalError(e.message);
    }
  });

  $("btn-context").addEventListener("click", loadContext);
  $("btn-seed").addEventListener("click", loadDemo);
  $("btn-back").addEventListener("click", () => {
    $("view-topic").classList.add("hidden");
    $("view-topics").classList.remove("hidden");
    loadTopics();
  });
}

async function main() {
  wireForms();
  await checkHealth();
  await loadTopics();
  const deepLink = new URLSearchParams(window.location.search).get("topic");
  if (deepLink) openTopic(deepLink);
}

main();
