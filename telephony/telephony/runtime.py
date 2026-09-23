"""Factory for selecting the delegated-agent runtime from configuration."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .agents import AgentRequest, AgentRuntime, CallableAgentRuntime
from .config import TelephonySettings
from .langchain import LangChainAgentRuntime, RunnableInputFactory
from .langgraph import GraphStateFactory, LangGraphAgentRuntime
from .ports import CallContext
from .responses import ResponsesAgentRuntime
from .tools import ToolRegistry

CallableAgent = Callable[[AgentRequest, CallContext], Any]


def build_runtime(
    settings: TelephonySettings,
    registry: ToolRegistry,
    *,
    client: Any | None = None,
    runnable: Any | None = None,
    graph: Any | None = None,
    checkpointer: Any | None = None,
    input_factory: RunnableInputFactory | None = None,
    state_factory: GraphStateFactory | None = None,
    callable_agent: CallableAgent | None = None,
) -> AgentRuntime:
    """Build the configured backend adapter with explicit app dependencies.

    ``responses`` works with only the shared :class:`ToolRegistry`. LangChain
    and LangGraph have application-specific runnable/graph definitions, so the
    factory rejects missing dependencies early with an actionable error.
    """

    match settings.agent_runtime:
        case "responses":
            return ResponsesAgentRuntime(registry, settings=settings, client=client)
        case "langchain":
            if runnable is None:
                raise ValueError(
                    "TELEPHONY_AGENT_RUNTIME=langchain requires a runnable= argument"
                )
            return LangChainAgentRuntime(
                runnable, registry=registry, input_factory=input_factory
            )
        case "langgraph":
            if graph is None:
                raise ValueError(
                    "TELEPHONY_AGENT_RUNTIME=langgraph requires a graph= argument"
                )
            return LangGraphAgentRuntime(
                graph, checkpointer=checkpointer, state_factory=state_factory
            )
        case "callable":
            if callable_agent is None:
                raise ValueError(
                    "TELEPHONY_AGENT_RUNTIME=callable requires a callable_agent= argument"
                )
            return CallableAgentRuntime(callable_agent)
