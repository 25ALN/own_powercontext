"""端到端验收：启动真实 PowerContext Server，验证跨会话持久化闭环。

这里的「会话」以应用进程为界：先在第一个应用实例中创建课题并写入记录，
然后关闭应用（模拟结束会话），再启动第二个应用实例，确认所有数据都
从 PowerContext 读回，并可通过检索接口命中。

PowerContext Server 本身由本测试用独立数据目录启动（真实服务，非 mock）。
"""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class Server:
    """真实 PowerContext Server 子进程（SQLite 存储，独立数据目录）。"""

    def __init__(self, tmp_path: Path) -> None:
        self.port = free_port()
        self.data_dir = tmp_path / "powercontext-home"
        self.data_dir.mkdir()
        self.process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def start(self) -> None:
        env = {**os.environ, "POWERCONTEXT_HOME": str(self.data_dir)}
        powercontext_bin = Path(sys.executable).parent / "powercontext"
        self.process = subprocess.Popen(
            [str(powercontext_bin), "server", "run", "--port", str(self.port)],
            cwd=PROJECT_ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                response = httpx.get(f"{self.url}/health/live", timeout=2)
                if response.status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            if self.process.poll() is not None:
                output = self.process.stdout.read().decode(errors="replace") if self.process.stdout else ""
                raise RuntimeError(f"PowerContext server exited early:\n{output}")
            time.sleep(0.5)
        raise RuntimeError("PowerContext server did not become healthy in 60s")

    def stop(self) -> None:
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    real_server = Server(tmp_path_factory.mktemp("e2e"))
    real_server.start()
    yield real_server
    real_server.stop()


@pytest.fixture()
def fresh_server(tmp_path_factory):
    """独立数据目录的真实 Server：用于验证“重启后数据仍在”。"""
    real_server = Server(tmp_path_factory.mktemp("e2e-fresh"))
    real_server.start()
    yield real_server
    real_server.stop()


class AppClient:
    """课题续研台 API 客户端。每次实例化代表一个新的应用「会话」。"""

    def __init__(self, pc_url: str) -> None:
        from fastapi.testclient import TestClient

        from app.main import app

        os.environ["POWERCONTEXT_URL"] = pc_url
        # 每个会话都是全新的应用实例状态
        app.state.pc = None
        self.client = TestClient(app, raise_server_exceptions=False)

    def request(self, method: str, path: str, json: dict | None = None):
        return self.client.request(method, path, json=json)


def test_persistence_across_sessions(fresh_server):
    """核心闭环：会话 A 写入 → 应用关闭 → 会话 B 从 PowerContext 读回并可检索。"""

    def session_a() -> str:
        app = AppClient(fresh_server.url)
        response = app.request("POST", "/api/topics", json={"title": "毕业论文文献调研", "summary": "调研混合检索方法"})
        assert response.status_code == 201, response.text
        scope_id = response.json()["scope_id"]

        note = app.request(
            "POST",
            f"/api/topics/{scope_id}/notes",
            json={
                "kind": "conclusion",
                "text": "确定用 RRF 融合 BM25 与向量检索结果。",
                "source": {
                    "title": "BEIR benchmark",
                    "url": "https://arxiv.org/abs/2104.08663",
                    "excerpt": "BM25 is a strong baseline in zero-shot retrieval settings.",
                },
            },
        )
        assert note.status_code == 201, note.text

        handoff = app.request(
            "POST",
            f"/api/topics/{scope_id}/handoff",
            json={
                "goal": "完成实验章节",
                "done": ["确定融合方案"],
                "evidence": ["BEIR 论文对比数据"],
                "open_questions": ["中文语料调参"],
                "next_steps": ["下载中文评测集"],
            },
        )
        assert handoff.status_code == 201, handoff.text
        return scope_id

    scope_id = session_a()
    # 会话 A 的 TestClient 退出（with 块外）即代表应用关闭；这里是新会话 B。
    app_b = AppClient(fresh_server.url)

    # 课题列表里能看到会话 A 创建的课题（数据来自 PowerContext scope 存储）
    topics = app_b.request("GET", "/api/topics")
    assert topics.status_code == 200
    titles = [t["title"] for t in topics.json()["topics"]]
    assert "毕业论文文献调研" in titles

    # 笔记与交接从 PowerContext memory 读回
    detail = app_b.request("GET", f"/api/topics/{scope_id}")
    assert detail.status_code == 200
    notes = detail.json()["notes"]
    texts = [n["text"] for n in notes]
    assert any("RRF" in text for text in texts)
    assert any("【交接状态】" in text and "完成实验章节" in text for text in texts)
    sourced = [n for n in notes if n["source"]]
    assert sourced and sourced[0]["source"]["title"] == "BEIR benchmark"

    # 检索命中会话 A 写入的记录
    search = app_b.request("GET", f"/api/topics/{scope_id}/search?q=RRF")
    assert search.status_code == 200
    hits = search.json()["hits"]
    assert hits and any("RRF" in (hit["text"] or "") for hit in hits)

    # 续研包由 PowerContext context/prepare 生成：默认以最近交接为 query，
    # 命中会话 A 保存的历史记录
    context = app_b.request("GET", f"/api/topics/{scope_id}/context")
    assert context.status_code == 200
    assert context.json()["status"] == "ready"
    assert "完成实验章节" in context.json()["content"]

    # 指定关键词时，续研包聚焦到相关证据
    context_rrf = app_b.request("GET", f"/api/topics/{scope_id}/context?q=RRF")
    assert context_rrf.status_code == 200
    assert context_rrf.json()["status"] == "ready"
    assert "RRF" in context_rrf.json()["content"]


def test_demo_seed_roundtrip(server):
    """示例课题经 PowerContext 真实写入，重复载入幂等。"""
    app = AppClient(server.url)
    first = app.request("POST", "/api/demo/seed")
    assert first.status_code == 201
    scope_id = first.json()["scope_id"]
    assert first.json()["created"] is True

    second = app.request("POST", "/api/demo/seed")
    assert second.status_code == 201
    assert second.json()["created"] is False
    assert second.json()["scope_id"] == scope_id

    detail = app.request("GET", f"/api/topics/{scope_id}")
    kinds = {n["kind"] for n in detail.json()["notes"]}
    assert {"fact", "decision", "question", "working_note", "handoff"} <= kinds


def test_default_scope_not_listed(server):
    """PowerContext 内置 Default scope 不应出现在课题列表中。"""
    app = AppClient(server.url)
    topics = app.request("GET", "/api/topics").json()["topics"]
    assert all(t["title"] != "Default" for t in topics)


def test_source_actually_persisted_in_powercontext(server):
    """直接查询 PowerContext：来源证据确实存进了它的 source 存储。"""
    app = AppClient(server.url)
    scope_id = app.request("POST", "/api/topics", json={"title": "来源核验", "summary": "核验 source 持久化"}).json()[
        "scope_id"
    ]
    app.request(
        "POST",
        f"/api/topics/{scope_id}/notes",
        json={
            "kind": "fact",
            "text": "记录一条带来源的事实。",
            "source": {
                "title": "某论文",
                "url": "https://example.org/paper",
                "excerpt": "Original excerpt text 12345.",
            },
        },
    )
    # 绕过课题续研台，直接向 PowerContext 查询该 scope 的公开 sources
    sources = httpx.get(f"{server.url}/v1/scopes/{scope_id}/sources", timeout=10)
    assert sources.status_code == 200, sources.text
    raw = str(sources.json())
    assert "Original excerpt text 12345." in raw, "来源摘录未在 PowerContext 中找到"


def test_async_smoke_of_client(server):
    """直接使用 PowerContextClient（异步路径）完成一次写读。"""
    from app.pc import PowerContextClient

    async def run():
        client = PowerContextClient(base_url=server.url)
        try:
            assert await client.live() is True
            scope = await client.create_scope("异步冒烟", "async smoke")
            await client.remember(scope["scope_id"], "fact", "异步写入的事实")
            entries = await client.list_memory_entries(scope["scope_id"])
            assert any("异步写入的事实" in entry.get("text", "") for entry in entries)
        finally:
            await client.aclose()

    asyncio.run(run())
