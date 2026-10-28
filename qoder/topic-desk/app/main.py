"""课题续研台后端。

所有持久化都通过 PowerContext 完成：
- 课题 = PowerContext scope（数据分区与身份）
- 笔记/结论/问题/进展 = memory entry（显式 remember 写入）
- 来源证据 = content source（/v1/sources/content 采集）
- 续研包 = /v1/context/prepare 的有界历史上下文
- 检索 = /v1/memory/search

应用本身无状态，重启后所有课题数据都从 PowerContext 读回。
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .pc import PowerContextClient, PowerContextError, PowerContextUnavailable

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

NOTE_KINDS: dict[str, str] = {
    "conclusion": "decision",
    "fact": "fact",
    "question": "question",
    "progress": "working_note",
}
KIND_LABELS: dict[str, str] = {
    "decision": "结论",
    "fact": "事实",
    "question": "问题",
    "working_note": "进展",
    "handoff": "交接",
}


class TopicCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=500)


class NoteSource(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    url: str = Field(default="", max_length=500)
    excerpt: str = Field(min_length=1, max_length=5000)


class NoteCreate(BaseModel):
    kind: Literal["conclusion", "fact", "question", "progress"]
    text: str = Field(min_length=1, max_length=4000)
    source: NoteSource | None = None


class HandoffCreate(BaseModel):
    goal: str = Field(min_length=1, max_length=500)
    done: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[str] = Field(default_factory=list, max_length=20)
    open_questions: list[str] = Field(default_factory=list, max_length=20)
    next_steps: list[str] = Field(default_factory=list, max_length=20)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.pc = PowerContextClient()
    yield
    await app.state.pc.aclose()


app = FastAPI(title="课题续研台", version="0.1.0", lifespan=lifespan)


def get_client(request: Request) -> PowerContextClient:
    client = getattr(request.app.state, "pc", None)
    if client is None:
        client = PowerContextClient()
        request.app.state.pc = client
    return client


def error_response(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@app.exception_handler(PowerContextUnavailable)
async def unavailable_handler(request: Request, exc: PowerContextUnavailable) -> JSONResponse:
    return error_response(
        503,
        "powercontext_unavailable",
        f"{exc}。请先启动 PowerContext Server（uv run powercontext server run），并确认 POWERCONTEXT_URL 配置正确。",
    )


@app.exception_handler(PowerContextError)
async def backend_error_handler(request: Request, exc: PowerContextError) -> JSONResponse:
    return error_response(502, "powercontext_error", f"PowerContext 返回错误（HTTP {exc.status_code}）：{exc.detail}")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    client = get_client(request)
    reachable = await client.live()
    return {
        "app": "topic-desk",
        "powercontext_url": client.base_url,
        "powercontext_reachable": reachable,
    }


@app.get("/api/topics")
async def list_topics(request: Request) -> dict[str, Any]:
    client = get_client(request)
    scopes = await client.list_scopes()
    topics = [
        {
            "scope_id": item.get("scope_id"),
            "title": item.get("title"),
            "summary": item.get("summary"),
            "is_demo": bool(item.get("title", "").startswith("[示例]")),
        }
        for item in scopes
    ]
    return {"topics": topics}


@app.post("/api/topics", status_code=201)
async def create_topic(request: Request, body: TopicCreate) -> dict[str, Any]:
    client = get_client(request)
    result = await client.create_scope(body.title, body.summary)
    return {"scope_id": result.get("scope_id"), "title": body.title, "summary": body.summary}


@app.get("/api/topics/{scope_id}")
async def topic_detail(request: Request, scope_id: str, kind: str | None = None) -> dict[str, Any]:
    client = get_client(request)
    scope = await client.get_scope(scope_id)
    entries = await client.list_memory_entries(scope_id)
    notes = [_render_entry(entry) for entry in entries]
    if kind is not None:
        notes = [note for note in notes if note["kind"] == kind]
    return {
        "topic": {
            "scope_id": scope.get("scope_id", scope_id),
            "title": scope.get("title"),
            "summary": scope.get("summary"),
        },
        "notes": notes,
    }


def _render_entry(entry: dict[str, Any]) -> dict[str, Any]:
    artifact = entry.get("artifact") or {}
    kind = entry.get("kind", "")
    text = entry.get("text", "")
    source = None
    if "[[source:" in text:
        try:
            marker_start = text.index("[[source:")
            marker_end = text.index("]]", marker_start)
            source = json.loads(text[marker_start + len("[[source:") : marker_end])
            text = text[:marker_start].rstrip()
        except ValueError:
            source = None
    return {
        "entry_id": artifact.get("artifact_id"),
        "version": artifact.get("revision"),
        "kind": kind,
        "kind_label": KIND_LABELS.get(kind, kind),
        "state": entry.get("state"),
        "text": text,
        "source": source,
    }


def _entry_id(mutation: dict[str, Any]) -> str | None:
    records = mutation.get("records") or []
    if not records:
        return None
    return ((records[0].get("artifact") or {}).get("artifact_id")) or None


@app.post("/api/topics/{scope_id}/notes", status_code=201)
async def create_note(request: Request, scope_id: str, body: NoteCreate) -> dict[str, Any]:
    client = get_client(request)
    text = body.text
    if body.source:
        captured = await client.capture_content_source(
            scope_id,
            content=body.source.excerpt,
            metadata={"title": body.source.title, "url": body.source.url, "origin": "topic-desk"},
        )
        source_ref = captured.get("source") or {}
        source_block = {
            "source_id": source_ref.get("source_id"),
            "title": body.source.title,
            "url": body.source.url,
        }
        text = f"{text}\n[[source:{json.dumps(source_block, ensure_ascii=False)}]]"
    remembered = await client.remember(scope_id, NOTE_KINDS[body.kind], text, reason="课题续研台：用户手动记录")
    return {"entry_id": _entry_id(remembered), "kind": NOTE_KINDS[body.kind]}


@app.post("/api/topics/{scope_id}/handoff", status_code=201)
async def create_handoff(request: Request, scope_id: str, body: HandoffCreate) -> dict[str, Any]:
    client = get_client(request)
    lines = [f"目标：{body.goal}"]
    if body.done:
        lines.append("已完成：" + "；".join(body.done))
    if body.evidence:
        lines.append("关键证据/来源：" + "；".join(body.evidence))
    if body.open_questions:
        lines.append("待解决问题：" + "；".join(body.open_questions))
    if body.next_steps:
        lines.append("下一步：" + "；".join(body.next_steps))
    text = "【交接状态】" + " ".join(lines)
    remembered = await client.remember(scope_id, "handoff", text, reason="课题续研台：会话交接整理")
    return {"entry_id": _entry_id(remembered), "sections": len(lines)}


@app.get("/api/topics/{scope_id}/context")
async def prepared_context(request: Request, scope_id: str, q: str = "") -> dict[str, Any]:
    client = get_client(request)
    scope = await client.get_scope(scope_id)
    # context/prepare 按 query 相关性选取证据。未指定 query 时，用最近的
    # 交接条目（或首条记录）作为 query，使“续研包”默认聚焦当前进展。
    if q:
        query = q
    else:
        entries = await client.list_memory_entries(scope_id)
        handoff = next((e for e in entries if e.get("kind") == "handoff"), None)
        seed_entry = handoff or (entries[0] if entries else None)
        query = (seed_entry or {}).get("text", "")[:200] or scope.get("summary") or scope.get("title") or "课题"
    prepared = await client.prepare_context(scope_id, query)
    return {
        "query": query,
        "status": prepared.get("status"),
        "content": prepared.get("content"),
        "content_bytes": prepared.get("content_bytes"),
    }


@app.get("/api/topics/{scope_id}/search")
async def search(request: Request, scope_id: str, q: str) -> dict[str, Any]:
    client = get_client(request)
    result = await client.search_memory(scope_id, q, limit=8)
    hits = []
    for hit in result.get("hits", []):
        memory = hit.get("memory") or {}
        hits.append(
            {
                "text": memory.get("text"),
                "kind": memory.get("kind"),
                "score": hit.get("score"),
                "entry_id": (memory.get("artifact") or {}).get("artifact_id"),
            }
        )
    return {"query": q, "mode": result.get("mode"), "hits": hits}


DEMO_TOPIC = {
    "title": "[示例] 混合检索实验续研",
    "summary": "调研并复现向量+全文混合检索方案，为课程项目准备实验对比（示例数据，可随时删除）",
}

DEMO_NOTES: list[dict[str, Any]] = [
    {
        "kind": "fact",
        "text": "BM25 在短查询上召回率明显高于纯向量检索，论文中的 MAP 差距约 8%。",
        "source": {
            "title": "BEIR: A Heterogeneous Benchmark for Zero-shot Evaluation",
            "url": "https://arxiv.org/abs/2104.08663",
            "excerpt": "BM25 is a strong baseline that outperforms dense retrieval on many BEIR datasets in zero-shot settings.",
        },
    },
    {
        "kind": "conclusion",
        "text": "课程项目采用 BM25 + 向量分数加权融合（RRF），不做端到端训练。",
    },
    {
        "kind": "question",
        "text": "RRF 的 k 参数在中文语料上是否需要重新调参？待查相关文献。",
    },
    {
        "kind": "progress",
        "text": "已在本地跑通 BEIR 环境的安装与数据下载，nq 子集已缓存。",
    },
]

DEMO_HANDOFF = {
    "goal": "完成混合检索实验对比并撰写课程报告的实验章节",
    "done": ["复现 BM25 基线", "确定 RRF 融合方案"],
    "evidence": ["BEIR 论文 Table 2：BM25 在 nq 上优于多个稠密检索基线"],
    "open_questions": ["RRF k 参数在中文语料上的取值"],
    "next_steps": ["下载中文检索评测集", "跑 RRF 融合实验并记录指标"],
}


@app.post("/api/demo/seed", status_code=201)
async def seed_demo(request: Request) -> dict[str, Any]:
    """一键载入示例课题。示例数据同样经 PowerContext 真实写入，可被检索与读回。"""
    client = get_client(request)
    existing = await client.list_scopes()
    if any(item.get("title") == DEMO_TOPIC["title"] for item in existing):
        scope_id = next(item["scope_id"] for item in existing if item.get("title") == DEMO_TOPIC["title"])
        return {"scope_id": scope_id, "created": False}
    created = await client.create_scope(DEMO_TOPIC["title"], DEMO_TOPIC["summary"])
    scope_id = created.get("scope_id")
    for note in DEMO_NOTES:
        source = note.get("source")
        text = note["text"]
        if source:
            captured = await client.capture_content_source(
                scope_id,
                content=source["excerpt"],
                metadata={"title": source["title"], "url": source["url"], "origin": "topic-desk-demo"},
            )
            source_block = {
                "source_id": (captured.get("source") or {}).get("source_id"),
                "title": source["title"],
                "url": source["url"],
            }
            text = f"{text}\n[[source:{json.dumps(source_block, ensure_ascii=False)}]]"
        await client.remember(scope_id, NOTE_KINDS[note["kind"]], text, reason="课题续研台：示例数据")
    handoff_text = (
        "【交接状态】目标：{goal} 已完成：{done} 关键证据/来源：{evidence} 待解决问题：{open} 下一步：{next}".format(
            goal=DEMO_HANDOFF["goal"],
            done="；".join(DEMO_HANDOFF["done"]),
            evidence="；".join(DEMO_HANDOFF["evidence"]),
            open="；".join(DEMO_HANDOFF["open_questions"]),
            next="；".join(DEMO_HANDOFF["next_steps"]),
        )
    )
    await client.remember(scope_id, "handoff", handoff_text, reason="课题续研台：示例交接")
    return {"scope_id": scope_id, "created": True}
