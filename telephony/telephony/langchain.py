"""LangChain runtime adapter for delegated GPT-Live work.

The adapter is deliberately thin: a caller supplies its LangChain runnable,
while this module translates its events into the framework-neutral
``AgentUpdate`` contract.  Tools are created per call so every invocation
still crosses :class:`telephony.tools.ToolRegistry` with the active scenario
and call limits.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from .agents import (
    AgentRequest,
    AgentUpdate,
    CommentaryUpdate,
    TaskCompleted,
    TaskFailed,
    ThinkingUpdate,
)
from .ports import CallContext
from .tools import ToolError, ToolRegistry, ToolSpec

RunnableInputFactory = Callable[[AgentRequest, CallContext], Any]

# Never spoken to the caller: raw tool JSON, and the prompt we sent ourselves.
_SKIP_ROLES = frozenset({"tool", "function", "user", "human", "system"})


class LangChainAgentRuntime:
    """Adapt a LangChain runnable without coupling the phone layer to it.

    The runnable must implement ``astream_events(input, version=\"v2\")``.
    If a registry is supplied, it must also support ``bind_tools``; this
    prevents tools from bypassing the registry's allowlist and quotas.
    ``input_factory`` makes the adapter usable with an existing prompt or
    graph state shape while the default provides LangChain message dicts.
    """

    def __init__(
        self,
        runnable: Any,
        *,
        registry: ToolRegistry | None = None,
        input_factory: RunnableInputFactory | None = None,
    ) -> None:
        if not hasattr(runnable, "astream_events"):
            raise TypeError("runnable must provide astream_events()")
        self._runnable = runnable
        self._registry = registry
        self._input_factory = input_factory or self._default_input

    async def run(
        self, request: AgentRequest, context: CallContext
    ) -> AsyncIterator[AgentUpdate]:
        """Stream private model progress, then the final caller-facing answer."""

        try:
            runnable = self._bound_runnable(context)
            final_output: Any = None
            async for event in runnable.astream_events(
                self._input_factory(request, context), version="v2"
            ):
                event_name = self._field(event, "event")
                data = self._field(event, "data")
                if event_name == "on_chat_model_stream":
                    text = self._text(self._field(data, "chunk"))
                    if text:
                        yield ThinkingUpdate(text)
                elif event_name == "on_tool_start":
                    name = self._field(event, "name")
                    if isinstance(name, str) and name:
                        yield ThinkingUpdate(f"Using tool: {name}")
                elif (event_name == "on_chain_end" and not self._field(event, "parent_ids")) or (
                    event_name == "on_chat_model_end"
                ):
                    final_output = self._field(data, "output")

            text = self._text(final_output)
            if text:
                yield CommentaryUpdate(text)
            yield TaskCompleted(text or None)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield TaskFailed(f"LangChain runtime failed: {exc}")

    def _bound_runnable(self, context: CallContext) -> Any:
        if self._registry is None:
            return self._runnable
        specs = self._registry.specs(context.scenario)
        if not specs:
            return self._runnable
        bind_tools = getattr(self._runnable, "bind_tools", None)
        if not callable(bind_tools):
            raise TypeError("runnable must provide bind_tools() when a ToolRegistry is used")
        return bind_tools(self._langchain_tools(specs, context))

    def _langchain_tools(self, specs: list[ToolSpec], context: CallContext) -> list[Any]:
        try:
            from langchain_core.tools import StructuredTool
        except ImportError as exc:  # pragma: no cover - depends on extras selection
            raise RuntimeError(
                "Install telephony with the 'langchain' extra to use ToolRegistry bindings"
            ) from exc

        tools: list[Any] = []
        for spec in specs:
            tools.append(
                StructuredTool.from_function(
                    coroutine=self._tool_handler(spec, context),
                    name=spec.name,
                    description=spec.description,
                    args_schema=spec.parameters,
                )
            )
        return tools

    def _tool_handler(self, spec: ToolSpec, context: CallContext) -> Callable[..., Any]:
        async def invoke(**arguments: Any) -> str:
            try:
                result = await self._registry.invoke(spec.name, arguments, context)  # type: ignore[union-attr]
                payload: Any = {"result": result}
            except ToolError as exc:
                # The model receives a safe failure result and can recover;
                # implementation details never become caller-facing speech.
                payload = {"error": str(exc)}
            return json.dumps(payload, default=str)

        return invoke

    @staticmethod
    def _default_input(request: AgentRequest, context: CallContext) -> dict[str, Any]:
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
        return {"messages": messages}

    @classmethod
    def _text(cls, value: Any) -> str:
        if isinstance(value, str):
            return value
        if cls._field(value, "role") in _SKIP_ROLES or cls._field(value, "type") in _SKIP_ROLES:
            # A tool result is the newest message in an agent's final state, but
            # it is raw JSON: speaking it would read the payload to the caller.
            return ""
        if isinstance(value, Mapping):
            content = value.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(cls._text(item) for item in content)
            # A Responses-API content block carries its payload under "text".
            text = value.get("text")
            if isinstance(text, str):
                return text
            messages = value.get("messages")
            if isinstance(messages, (list, tuple)):
                for message in reversed(messages):
                    text = cls._text(message)
                    if text:
                        return text
            return ""
        content = getattr(value, "content", None)
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "".join(cls._text(item) for item in content)
        messages = getattr(value, "messages", None)
        if isinstance(messages, (list, tuple)):
            for message in reversed(messages):
                text = cls._text(message)
                if text:
                    return text
        return ""

    @staticmethod
    def _field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
