"""OpenAI Responses backend for GPT-Live client delegations.

The Live session remains responsible for the voice conversation.  This
runtime receives only a delegated backend task, streams its verified text
back as commentary, and executes application tools through :mod:`tools`.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any

from .agents import AgentRequest, AgentUpdate, CommentaryUpdate, TaskCompleted, TaskFailed
from .config import TelephonySettings, get_settings
from .ports import CallContext
from .tools import ToolError, ToolRegistry, ToolSpec


class ResponsesAgentRuntime:
    """Run delegated work through the Responses API and the local tool policy.

    ``client`` is injectable to keep the runtime straightforward to exercise
    without credentials.  When omitted, an ``AsyncOpenAI`` client is created
    only on first use, so importing ``telephony`` never requires its optional
    OpenAI dependency.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        settings: TelephonySettings | None = None,
        client: Any | None = None,
    ) -> None:
        self._registry = registry
        self._settings = settings or get_settings()
        self._client = client

    async def run(
        self, request: AgentRequest, context: CallContext
    ) -> AsyncIterator[AgentUpdate]:
        """Stream commentary and keep following model-requested tool calls."""

        input_items: list[Any] = self._initial_input(request, context)
        tools = [self._tool_definition(spec) for spec in self._registry.specs(context.scenario)]
        latest_text = ""

        while True:
            response, spoken = await self._stream_response(
                input_items=input_items,
                tools=tools,
            )
            latest_text = self._response_text(response) or latest_text
            # One append per answer, not per token: GPT-Live speaks each
            # ``session.commentary.append`` as its own piece of content.
            if spoken:
                yield CommentaryUpdate(spoken)

            if self._response_status(response) not in (None, "completed"):
                yield TaskFailed(self._response_error(response))
                return

            function_calls = tuple(self._function_calls(response))
            if not function_calls:
                if latest_text and not spoken:
                    yield CommentaryUpdate(latest_text)
                yield TaskCompleted(latest_text or None)
                return

            input_items.extend(self._response_output(response))
            input_items.extend(
                [await self._tool_output(call, context) for call in function_calls]
            )

    async def _stream_response(
        self, *, input_items: list[Any], tools: list[dict[str, Any]]
    ) -> tuple[Any, str]:
        create = self._get_client().responses.create
        stream = create(
            model=self._settings.backend_model,
            instructions=self._settings.backend_instructions,
            input=input_items,
            tools=tools,
            stream=True,
        )
        if inspect.isawaitable(stream):
            stream = await stream
        if not hasattr(stream, "__aiter__"):
            raise RuntimeError("ResponsesAgentRuntime requires an asynchronous Responses client")

        response: Any | None = None
        text_deltas: list[str] = []
        async for event in stream:
            event_type = self._field(event, "type")
            if event_type == "response.output_text.delta":
                delta = self._field(event, "delta")
                if isinstance(delta, str) and delta:
                    text_deltas.append(delta)
            elif event_type in ("response.completed", "response.failed"):
                response = self._field(event, "response")

        if response is None:
            raise RuntimeError("Responses stream ended without a terminal response event")
        return response, "".join(text_deltas)

    async def _tool_output(self, call: Any, context: CallContext) -> dict[str, str]:
        name = self._field(call, "name")
        call_id = self._field(call, "call_id")
        raw_arguments = self._field(call, "arguments")
        if not isinstance(name, str) or not isinstance(call_id, str):
            raise RuntimeError("Responses returned a malformed function call")

        try:
            arguments = json.loads(raw_arguments)
            if not isinstance(arguments, Mapping):
                raise ValueError("arguments must be a JSON object")
            result = await self._registry.invoke(name, arguments, context)
            payload: Any = {"result": result}
        except (json.JSONDecodeError, TypeError, ValueError, ToolError) as exc:
            # Let the model recover or explain the unavailable operation;
            # do not expose the exception as spoken caller-facing text.
            payload = {"error": str(exc)}

        return {
            "type": "function_call_output",
            "call_id": call_id,
            "output": json.dumps(payload, default=str),
        }

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover - depends on extras selection
                raise RuntimeError(
                    "Install telephony with the 'openai' extra to use ResponsesAgentRuntime"
                ) from exc
            kwargs: dict[str, str] = {}
            if self._settings.openai_api_key:
                kwargs["api_key"] = self._settings.openai_api_key
            if self._settings.openai_base_url:
                kwargs["base_url"] = self._settings.openai_base_url
            self._client = AsyncOpenAI(**kwargs)
        return self._client

    @staticmethod
    def _initial_input(request: AgentRequest, context: CallContext) -> list[dict[str, str]]:
        messages = [
            {"role": fragment.speaker, "content": fragment.text}
            for fragment in context.transcript
            if fragment.text.strip()
        ]
        task = request.instructions or request.task.instructions
        if task.strip():
            messages.append({"role": "user", "content": f"Backend task: {task}"})
        if not messages:
            messages.append({"role": "user", "content": "Handle the current caller request."})
        return messages

    @staticmethod
    def _tool_definition(spec: ToolSpec) -> dict[str, Any]:
        return {
            "type": "function",
            "name": spec.name,
            "description": spec.description,
            "parameters": spec.parameters,
        }

    @classmethod
    def _function_calls(cls, response: Any) -> Iterable[Any]:
        return (
            item
            for item in cls._response_output(response)
            if cls._field(item, "type") == "function_call"
        )

    @classmethod
    def _response_output(cls, response: Any) -> list[Any]:
        output = cls._field(response, "output")
        return list(output) if isinstance(output, Iterable) and not isinstance(output, str) else []

    @classmethod
    def _response_text(cls, response: Any) -> str:
        output_text = cls._field(response, "output_text")
        if isinstance(output_text, str):
            return output_text
        text: list[str] = []
        for item in cls._response_output(response):
            if cls._field(item, "type") != "message":
                continue
            for content in cls._field(item, "content") or []:
                if cls._field(content, "type") == "output_text":
                    value = cls._field(content, "text")
                    if isinstance(value, str):
                        text.append(value)
        return "".join(text)

    @classmethod
    def _response_status(cls, response: Any) -> str | None:
        value = cls._field(response, "status")
        return value if isinstance(value, str) else None

    @classmethod
    def _response_error(cls, response: Any) -> str:
        error = cls._field(response, "error")
        if isinstance(error, Mapping):
            message = error.get("message")
            if isinstance(message, str) and message:
                return message
        return "Responses request did not complete"

    @staticmethod
    def _field(value: Any, name: str) -> Any:
        return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)
