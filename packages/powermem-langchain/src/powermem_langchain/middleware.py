"""LangChain middleware entry point for PowerMem.

The middleware makes PowerMem a long-term memory layer for LangChain v1
agents:

* before the agent starts, the latest user message is used to search PowerMem
  for relevant memories;
* the retrieved memories are injected into the model-visible context for every
  model call (as an additional system message);
* after the agent finishes, the user/assistant interaction is written back to
  PowerMem when persistence is enabled;
* a failing memory backend never breaks the agent: search and write-back
  errors are logged and swallowed (fail-open).
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, NotRequired, TypedDict

from langchain.agents.middleware import AgentMiddleware, AgentState
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage

if TYPE_CHECKING:  # pragma: no cover - typing only
    from powermem import Memory

logger = logging.getLogger(__name__)

DEFAULT_CONTEXT_HEADER = (
    "Relevant long-term memories for this user (retrieved from PowerMem). "
    "Use them when they help answer the current request."
)


def _message_text(message: BaseMessage) -> str:
    """Return plain text for a LangChain message with string or block content."""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _state_messages(state: Any) -> list[BaseMessage]:
    messages = state.get("messages") if hasattr(state, "get") else None
    return list(messages or [])


class PowerMemState(AgentState):
    """Agent state extended with the memory context injected by the middleware."""

    powermem_context: NotRequired[str]


class PowerMemStateUpdate(TypedDict):
    """State update returned by the memory-loading middleware hooks."""

    powermem_context: str


class PowerMemMiddleware(AgentMiddleware[PowerMemState, Any, Any]):
    """Inject PowerMem memories into a LangChain agent and persist interactions.

    Example:
        ``PowerMemMiddleware(memory=create_memory(), user_id="user123")`` can be
        passed to ``langchain.agents.create_agent(middleware=[...])``.
    """

    state_schema = PowerMemState

    def __init__(
        self,
        *,
        memory: "Memory",
        user_id: str | None = None,
        search_limit: int = 5,
        save_interactions: bool = True,
        context_header: str = DEFAULT_CONTEXT_HEADER,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self.memory = memory
        self.user_id = user_id
        self.search_limit = max(int(search_limit), 1)
        self.save_interactions = save_interactions
        self.context_header = context_header

    # ------------------------------------------------------------------ search
    @staticmethod
    def _latest_user_text(messages: list[BaseMessage]) -> str:
        for message in reversed(messages):
            if isinstance(message, HumanMessage):
                return _message_text(message).strip()
        return ""

    def _search_memories(self, query: str) -> list[str]:
        if not query or self.memory is None:
            return []
        try:
            result = self.memory.search(
                query,
                user_id=self.user_id,
                limit=self.search_limit,
            )
        except Exception:  # noqa: BLE001 - memory failures must not break the agent
            logger.warning(
                "PowerMem memory search failed; continuing without memory context",
                exc_info=True,
            )
            return []
        memories: list[str] = []
        for item in (result or {}).get("results", []) or []:
            if not isinstance(item, dict):
                continue
            text = item.get("memory") or item.get("content") or ""
            if isinstance(text, str) and text.strip():
                memories.append(text.strip())
        return memories

    def _format_context(self, memories: list[str]) -> str:
        if not memories:
            return ""
        lines = [self.context_header, ""]
        lines.extend(f"- {memory}" for memory in memories)
        return "\n".join(lines)

    def _build_context(self, state: Any) -> str:
        latest = self._latest_user_text(_state_messages(state))
        return self._format_context(self._search_memories(latest))

    def _inject_context(self, request: Any) -> Any:
        state = getattr(request, "state", None) or {}
        context = state.get("powermem_context") if hasattr(state, "get") else None
        if not context:
            return request
        messages = list(getattr(request, "messages", None) or [])
        # Keep any leading system messages first, then add the memory block so the
        # model sees it as high-priority context before the conversation.
        leading_system: list[BaseMessage] = []
        remaining = messages
        while remaining and isinstance(remaining[0], SystemMessage):
            leading_system.append(remaining.pop(0))
        memory_message = SystemMessage(content=context)
        return request.override(messages=[*leading_system, memory_message, *remaining])

    # ------------------------------------------------------------------- write
    def _latest_exchange(self, messages: list[BaseMessage]) -> tuple[str, str]:
        assistant_text = ""
        user_text = ""
        for message in reversed(messages):
            if isinstance(message, AIMessage) and not assistant_text:
                assistant_text = _message_text(message).strip()
            elif isinstance(message, HumanMessage):
                user_text = _message_text(message).strip()
                break
        return user_text, assistant_text

    def _persist_interaction(self, state: Any) -> None:
        if not self.save_interactions or self.memory is None:
            return
        user_text, assistant_text = self._latest_exchange(_state_messages(state))
        if not user_text and not assistant_text:
            return
        try:
            if user_text:
                self.memory.add(user_text, user_id=self.user_id, infer=False)
            if assistant_text:
                self.memory.add(assistant_text, user_id=self.user_id, infer=False)
        except Exception:  # noqa: BLE001 - write-back failures must not break the agent
            logger.warning(
                "PowerMem interaction write-back failed",
                exc_info=True,
            )

    # --------------------------------------------------------------- lifecycle
    def before_agent(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> PowerMemStateUpdate | None:
        context = self._build_context(state)
        return {"powermem_context": context} if context else None

    async def abefore_agent(
        self,
        state: PowerMemState,
        runtime: Any,
    ) -> PowerMemStateUpdate | None:
        context = await asyncio.to_thread(self._build_context, state)
        return {"powermem_context": context} if context else None

    def wrap_model_call(self, request: Any, handler: Any) -> Any:
        return handler(self._inject_context(request))

    async def awrap_model_call(self, request: Any, handler: Any) -> Any:
        return await handler(self._inject_context(request))

    def after_agent(self, state: PowerMemState, runtime: Any) -> None:
        self._persist_interaction(state)
        return None

    async def aafter_agent(self, state: PowerMemState, runtime: Any) -> None:
        await asyncio.to_thread(self._persist_interaction, state)
        return None
