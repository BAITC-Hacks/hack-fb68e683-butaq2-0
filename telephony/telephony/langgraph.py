"""LangGraph runtime adapter for delegated GPT-Live work.

Each phone call is a LangGraph thread.  Each GPT-Live delegation receives a
separate checkpoint namespace inside that thread, so an interrupted task can
be resumed without leaking its state into the next caller request.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from .agents import AgentRequest, AgentUpdate, CommentaryUpdate, TaskCompleted, TaskFailed
from .ports import CallContext

GraphStateFactory = Callable[[AgentRequest, CallContext], Any]


class LangGraphAgentRuntime:
    """Adapt a LangGraph builder or compiled graph to :class:`AgentRuntime`.

    Pass a ``StateGraph`` builder to get an in-memory checkpointer by default,
    or pass a compiled graph when checkpoint persistence is owned elsewhere.
    The latter is the production path for a durable saver such as Redis.
    """

    def __init__(
        self,
        graph: Any,
        *,
        checkpointer: Any | None = None,
        state_factory: GraphStateFactory | None = None,
    ) -> None:
        self._graph = self._compile(graph, checkpointer)
        if not hasattr(self._graph, "astream"):
            raise TypeError("graph must provide astream() or compile()")
        self._state_factory = state_factory or self._default_state

    async def run(
        self, request: AgentRequest, context: CallContext
    ) -> AsyncIterator[AgentUpdate]:
        """Run one delegation in its isolated checkpoint namespace."""

        async for update in self._stream(
            self._state_factory(request, context), self.config_for(request, context)
        ):
            yield update

    async def resume(
        self,
        request: AgentRequest,
        context: CallContext,
        state_update: Mapping[str, Any],
    ) -> AsyncIterator[AgentUpdate]:
        """Apply a state update and continue the same delegated graph branch.

        This is intentionally outside ``AgentRuntime``: normal GPT-Live
        delegations start through :meth:`run`; an application only calls this
        after a graph interrupt or a human-in-the-loop action.
        """

        config = self.config_for(request, context)
        try:
            update_state = getattr(self._graph, "aupdate_state", None)
            if not callable(update_state):
                raise TypeError("graph must provide aupdate_state() to resume a delegation")
            await update_state(config, dict(state_update))
            async for update in self._stream(None, config):
                yield update
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield TaskFailed(f"LangGraph runtime failed: {exc}")

    async def _stream(self, state: Any, config: Mapping[str, Any]) -> AsyncIterator[AgentUpdate]:
        last_text = ""
        try:
            async for update in self._graph.astream(state, config, stream_mode="updates"):
                text = self._text(update)
                if text and text != last_text:
                    last_text = text
                    yield CommentaryUpdate(text)
            yield TaskCompleted(last_text or None)
        except asyncio.CancelledError:
            # DelegationExecutor owns cancellation and discards the superseded
            # task.  Re-raising prevents a late graph output from being spoken.
            raise
        except Exception as exc:
            yield TaskFailed(f"LangGraph runtime failed: {exc}")

    @staticmethod
    def config_for(request: AgentRequest, context: CallContext) -> dict[str, Any]:
        """Return the stable thread for one delegation of one call.

        ``checkpoint_ns`` is reserved by LangGraph for subgraph nesting: setting
        it here does *not* fork the thread, so a cancelled delegation's dangling
        tool calls leak into the next one. The delegation id therefore belongs in
        the thread id. Nothing is lost: the state factory rebuilds call-long
        context from ``context.transcript`` on every run.
        """

        return {
            "configurable": {
                "thread_id": f"{context.call_id}/{request.task.delegation_id}",
            }
        }

    @staticmethod
    def _compile(graph: Any, checkpointer: Any | None) -> Any:
        compile_graph = getattr(graph, "compile", None)
        if not callable(compile_graph):
            if checkpointer is not None:
                raise ValueError(
                    "checkpointer must be configured when compiling a graph, not after it"
                )
            return graph
        return compile_graph(checkpointer=checkpointer or _memory_checkpointer())

    @staticmethod
    def _default_state(request: AgentRequest, context: CallContext) -> dict[str, Any]:
        messages = [
            {"role": fragment.speaker, "content": fragment.text}
            for fragment in context.transcript
            if fragment.text.strip()
        ]
        instructions = request.instructions or request.task.instructions
        if instructions.strip():
            messages.append({"role": "user", "content": f"Backend task: {instructions}"})
        if not messages:
            messages.append({"role": "user", "content": "Handle the current caller request."})
        return {"messages": messages, "delegation": request.task.arguments}

    @classmethod
    def _text(cls, value: Any) -> str:
        """Extract the newest assistant-facing content from a graph update."""

        if isinstance(value, Mapping):
            messages = value.get("messages")
            if isinstance(messages, (list, tuple)):
                for message in reversed(messages):
                    text = cls._message_text(message)
                    if text:
                        return text
            for nested in reversed(tuple(value.values())):
                text = cls._text(nested)
                if text:
                    return text
            return ""
        messages = getattr(value, "messages", None)
        if isinstance(messages, (list, tuple)):
            for message in reversed(messages):
                text = cls._message_text(message)
                if text:
                    return text
        return ""

    @classmethod
    def _message_text(cls, message: Any) -> str:
        role = cls._field(message, "role") or cls._field(message, "type")
        if role in {"tool", "function"}:
            return ""
        content = cls._field(message, "content")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            return "".join(cls._message_text(part) for part in content).strip()
        text = cls._field(message, "text")
        if isinstance(text, str):
            return text.strip()
        if isinstance(message, str):
            return message.strip()
        return ""

    @staticmethod
    def _field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def _memory_checkpointer() -> Any:
    """Create the default saver lazily so importing telephony stays optional."""

    try:
        from langgraph.checkpoint.memory import InMemorySaver
    except ImportError as exc:  # pragma: no cover - depends on extras selection
        raise RuntimeError(
            "Install telephony with the 'langgraph' extra to compile a LangGraph builder"
        ) from exc
    return InMemorySaver()
