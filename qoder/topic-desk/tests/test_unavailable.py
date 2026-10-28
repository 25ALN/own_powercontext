"""单测：PowerContext 不可达时，用户能看到明确、可操作的错误。

这些测试只验证本地错误处理行为；真实后端的写入/读回验收见
tests/test_e2e_persistence.py，它启动真实的 PowerContext Server。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app

DEAD_URL = "http://127.0.0.1:59999"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("POWERCONTEXT_URL", DEAD_URL)
    app.state.pc = None
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_health_reports_unreachable(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["powercontext_reachable"] is False


def test_list_topics_returns_actionable_error(client):
    response = client.get("/api/topics")
    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "powercontext_unavailable"
    # 错误信息包含用户可执行的恢复步骤
    assert "powercontext server run" in body["error"]["message"]


def test_create_topic_returns_actionable_error(client):
    response = client.post("/api/topics", json={"title": "课题", "summary": "描述"})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "powercontext_unavailable"


def test_backend_http_error_is_distinct(client, monkeypatch):
    """PowerContext 返回 4xx/5xx 时应报 powercontext_error，与连接失败区分。"""
    from app.pc import PowerContextError

    async def fake_list_scopes(self):
        raise PowerContextError(400, "invalid payload")

    monkeypatch.setattr(type(app.state.pc), "list_scopes", fake_list_scopes)
    response = client.get("/api/topics")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "powercontext_error"


def test_connection_error_wrapped():
    import asyncio

    from app.pc import PowerContextClient, PowerContextUnavailable

    pc = PowerContextClient(base_url="http://127.0.0.1:59999")
    with pytest.raises(PowerContextUnavailable) as exc_info:
        asyncio.run(pc._request("GET", "/health/live"))
    assert "无法连接" in str(exc_info.value)
    assert "59999" in str(exc_info.value)
