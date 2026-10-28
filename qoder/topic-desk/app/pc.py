"""PowerContext Server 的轻量 HTTP 客户端。

课题续研台不自行持久化任何课题数据：课题（scope）、笔记与交接条目（memory
entry）、来源证据（content source）全部写入 PowerContext，并通过其检索与
上下文准备接口读回。跨会话的持久化由 PowerContext 的存储承担。
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx


class PowerContextUnavailable(RuntimeError):
    """无法连接 PowerContext Server 时抛出，向上转换为对用户可见的错误。"""


class PowerContextError(RuntimeError):
    """PowerContext 返回了非 2xx 响应。"""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(f"PowerContext returned HTTP {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class PowerContextClient:
    def __init__(self, base_url: str | None = None, token: str | None = None) -> None:
        self.base_url = (base_url or os.environ.get("POWERCONTEXT_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.token = token or os.environ.get("POWERCONTEXT_TOKEN") or ""

    def _new_client(self) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return httpx.AsyncClient(base_url=self.base_url, headers=headers, timeout=15.0, trust_env=False)

    async def aclose(self) -> None:
        return None

    async def _request(self, method: str, path: str, json: Any = None) -> dict[str, Any]:
        # 每次请求使用独立的短生命周期客户端：宿主可能在不同事件循环中
        # 处理请求（如 TestClient 的每个请求一个 loop），复用连接池会绑定到已关闭的 loop。
        async with self._new_client() as client:
            try:
                response = await client.request(method, path, json=json)
            except httpx.HTTPError as error:
                raise PowerContextUnavailable(
                    f"无法连接 PowerContext Server（{self.base_url}）：{error.__class__.__name__}"
                ) from error
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise PowerContextError(response.status_code, str(detail))
        if response.status_code == 204:
            return {}
        return response.json()

    async def live(self) -> bool:
        try:
            await self._request("GET", "/health/live")
            return True
        except (PowerContextUnavailable, PowerContextError):
            return False

    async def create_scope(self, title: str, summary: str) -> dict[str, Any]:
        return await self._request(
            "POST",
            "/v1/scopes",
            {
                "title": title,
                "summary": summary,
                # external_references 是自由字段：用固定 kind 标记课题续研台创建的 scope，
                # 以便与 PowerContext 内置 Default scope 区分。
                "external_references": [{"kind": "topic-desk", "value": "topic"}],
                "idempotency_key": f"topic-desk-{uuid.uuid4().hex}",
            },
        )

    async def list_scopes(self, limit: int = 100) -> list[dict[str, Any]]:
        async with self._new_client() as client:
            try:
                response = await client.get(
                    "/v1/scopes", params={"external_reference_kind": "topic-desk", "limit": limit}
                )
            except httpx.HTTPError as error:
                raise PowerContextUnavailable(
                    f"无法连接 PowerContext Server（{self.base_url}）：{error.__class__.__name__}"
                ) from error
        if response.status_code >= 400:
            raise PowerContextError(response.status_code, response.text)
        return response.json().get("items", [])

    async def get_scope(self, scope_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v1/scopes/{scope_id}")

    async def capture_content_source(
        self, scope_id: str, content: str, metadata: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "scope_id": scope_id,
            "source_id": f"topic-desk-source-{uuid.uuid4().hex[:12]}",
            "content": content,
        }
        if metadata:
            payload["metadata"] = metadata
        return await self._request("POST", "/v1/sources/content", payload)

    async def remember(self, scope_id: str, kind: str, text: str, reason: str = "") -> dict[str, Any]:
        payload: dict[str, Any] = {"scope_id": scope_id, "kind": kind, "text": text}
        if reason:
            payload["reason"] = reason
        return await self._request("POST", "/v1/memory/remember", payload)

    async def list_memory_entries(self, scope_id: str) -> list[dict[str, Any]]:
        result = await self._request("POST", "/v1/memory/entries/list", {"scope_id": scope_id})
        return result.get("entries", [])

    async def search_memory(self, scope_id: str, query: str, limit: int = 5) -> dict[str, Any]:
        return await self._request("POST", "/v1/memory/search", {"scope_id": scope_id, "query": query, "limit": limit})

    async def prepare_context(self, scope_id: str, query: str, max_bytes: int = 4000) -> dict[str, Any]:
        return await self._request(
            "POST", "/v1/context/prepare", {"scope_id": scope_id, "query": query, "max_bytes": max_bytes}
        )
