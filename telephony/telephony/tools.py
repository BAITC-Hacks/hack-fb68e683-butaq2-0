"""Framework-neutral tools and execution limits for phone agents.

The registry intentionally knows nothing about OpenAI, LangChain, or the
session graph.  Runtimes consume its specs, while every invocation passes
through the same allowlist, timeout, and concurrency rules.
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from .config import TelephonySettings, get_settings
from .ports import CallContext

ToolHandler = Callable[..., Any]


class ToolError(Exception):
    """Base error for a tool that could not be safely executed."""


class ToolNotFound(ToolError):
    """The requested tool is not registered."""


class ToolNotAllowed(ToolError):
    """The requested tool is outside the call scenario allowlist."""


class ToolCallLimitExceeded(ToolError):
    """The call has used all of its permitted tool invocations."""


class ToolInvocationError(ToolError):
    """A registered handler rejected its arguments or failed."""


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """A JSON-schema-described application capability.

    ``scenarios=None`` means the tool is available in every scenario.  A
    concrete set makes it opt-in for those scenarios only.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler = field(repr=False, compare=False)
    timeout: float = 10.0
    confirm: bool = False
    scenarios: frozenset[str] | None = None

    def available_in(self, scenario: str | None) -> bool:
        return self.scenarios is None or scenario in self.scenarios


class ToolRegistry:
    """Register application tools and execute them under shared safeguards.

    Tool handlers receive JSON-schema arguments as keyword arguments and the
    current :class:`CallContext` as an optional ``context`` keyword argument.
    A handler can be synchronous or asynchronous.
    """

    def __init__(self, settings: TelephonySettings | None = None) -> None:
        self._settings = settings or get_settings()
        self._tools: dict[str, ToolSpec] = {}
        self._semaphore = asyncio.Semaphore(self._settings.max_tool_concurrency)
        self._counter_lock = asyncio.Lock()
        # Kept here, not on CallContext: every delegation gets its own context
        # copy, so a per-call budget must outlive them. Cleared by forget_call.
        self._call_counts: dict[UUID, int] = {}

    def tool(
        self,
        name: str,
        *,
        description: str,
        parameters: Mapping[str, Any],
        timeout: float = 10.0,
        confirm: bool = False,
        scenarios: Collection[str] | None = None,
    ) -> Callable[[ToolHandler], ToolHandler]:
        """Register a handler with its LLM-facing JSON Schema.

        ``scenarios`` is an optional allowlist.  Omitting it exposes the tool
        to all call scenarios; passing ``[\"support\"]`` exposes it only there.
        """

        clean_name = self._validate_name(name)
        clean_description = self._validate_description(description)
        clean_parameters = self._validate_parameters(parameters)
        if timeout <= 0:
            raise ValueError("tool timeout must be positive")
        if not isinstance(confirm, bool):
            raise TypeError("confirm must be a bool")
        clean_scenarios = self._validate_scenarios(scenarios)

        def register(handler: ToolHandler) -> ToolHandler:
            if not callable(handler):
                raise TypeError("tool handler must be callable")
            if clean_name in self._tools:
                raise ValueError(f"tool already registered: {clean_name}")
            self._tools[clean_name] = ToolSpec(
                name=clean_name,
                description=clean_description,
                parameters=clean_parameters,
                handler=handler,
                timeout=timeout,
                confirm=confirm,
                scenarios=clean_scenarios,
            )
            return handler

        return register

    def specs(self, scenario: str | None = None) -> list[ToolSpec]:
        """Return the tools the given call scenario is allowed to use."""

        return [spec for spec in self._tools.values() if spec.available_in(scenario)]

    async def invoke(
        self,
        name: str,
        arguments: Mapping[str, Any],
        context: CallContext,
    ) -> Any:
        """Run one tool with scenario, per-call, global, and timeout guards."""

        spec = self._get_allowed(name, context.scenario)
        if not isinstance(arguments, Mapping):
            raise ToolInvocationError("tool arguments must be an object")
        await self._consume_call_quota(context)

        try:
            async with self._semaphore:
                return await asyncio.wait_for(
                    self._call_handler(spec, dict(arguments), context),
                    timeout=spec.timeout,
                )
        except asyncio.TimeoutError as exc:
            raise ToolInvocationError(f"tool timed out after {spec.timeout:g}s: {name}") from exc
        except ToolError:
            raise
        except Exception as exc:
            raise ToolInvocationError(f"tool failed: {name}: {exc}") from exc

    def _get_allowed(self, name: str, scenario: str | None) -> ToolSpec:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolNotFound(f"unknown tool: {name}")
        if not spec.available_in(scenario):
            raise ToolNotAllowed(f"tool {name!r} is not allowed for scenario {scenario!r}")
        return spec

    def forget_call(self, call_id: UUID) -> None:
        """Release the per-call budget once the call is over."""

        self._call_counts.pop(call_id, None)

    async def _consume_call_quota(self, context: CallContext) -> None:
        async with self._counter_lock:
            count = self._call_counts.get(context.call_id, 0)
            if count >= self._settings.max_tool_calls_per_call:
                raise ToolCallLimitExceeded("tool call limit reached for this call")
            self._call_counts[context.call_id] = count + 1

    @staticmethod
    async def _call_handler(
        spec: ToolSpec, arguments: dict[str, Any], context: CallContext
    ) -> Any:
        kwargs = dict(arguments)
        if "context" in inspect.signature(spec.handler).parameters:
            if "context" in kwargs:
                raise ToolInvocationError("tool arguments must not contain reserved key: context")
            kwargs["context"] = context
        result = spec.handler(**kwargs)
        if inspect.isawaitable(result):
            return await result
        return result

    @staticmethod
    def _validate_name(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("tool name must not be empty")
        return value.strip()

    @staticmethod
    def _validate_description(value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("tool description must not be empty")
        return value.strip()

    @staticmethod
    def _validate_parameters(value: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(value, Mapping):
            raise TypeError("tool parameters must be a JSON Schema object")
        schema = dict(value)
        if schema.get("type") not in (None, "object"):
            raise ValueError("tool parameters schema must describe an object")
        return schema

    @staticmethod
    def _validate_scenarios(value: Collection[str] | None) -> frozenset[str] | None:
        if value is None:
            return None
        scenarios = frozenset(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        )
        if not scenarios or len(scenarios) != len(value):
            raise ValueError("scenarios must contain only non-empty strings")
        return scenarios
