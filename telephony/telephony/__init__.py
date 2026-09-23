"""telephony -- drop-in phone calls for FastAPI: GPT-Live + Twilio.

Twilio carries the audio, ``gpt-live-1`` holds the conversation, and your
application keeps the reasoning, tools and policy via client delegation.

Quick start::

    from fastapi import FastAPI
    from telephony import Telephony, create_telephony_router, register_exception_handlers
    from telephony.twilio import TwilioProvider

    telephony = Telephony(provider=TwilioProvider())
    app = FastAPI()
    app.include_router(create_telephony_router(telephony=telephony))
    register_exception_handlers(app)

Every dependency is a Protocol port; the in-memory adapters are the default
profile for tests and single-process demos.
"""

from .agents import (
    AgentRequest,
    AgentRuntime,
    AgentUpdate,
    CallableAgentRuntime,
    CommentaryUpdate,
    InstructionUpdate,
    InvalidAgentUpdate,
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    ThinkingUpdate,
    to_live_event,
)
from .application import Telephony
from .config import TelephonySettings, get_settings
from .delegation import DelegationExecutor, LiveFinalizationResult, LiveSessionCoordinator
from .domain import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATES,
    Call,
    CallDirection,
    CallNotFound,
    CallState,
    CallTransition,
    DelegationTask,
    InvalidNumber,
    InvalidTransition,
    PolicyDenied,
    TelephonyError,
    TranscriptFragment,
    Usage,
    WebhookVerificationError,
    check_transfer_target,
    mask_number,
    new_call,
    next_state,
    normalize_e164,
)
from .langchain import LangChainAgentRuntime
from .langgraph import LangGraphAgentRuntime
from .memory import (
    FakeProvider,
    InMemoryCallRepository,
    InMemoryEventInbox,
    InMemoryLiveStateStore,
)
from .openai_live import (
    OpenAILiveConnection,
    OpenAILiveGateway,
    OpenAILiveSessionBuilder,
    OpenAILiveTelephony,
)
from .ports import (
    AuthorizationPolicy,
    CallContext,
    CallEventSink,
    CallQuery,
    CallRepository,
    EventInbox,
    InboundRoutingPolicy,
    LiveCallController,
    LiveGateway,
    LiveIncomingCall,
    LiveSessionRunnerPort,
    LiveStateStore,
    OutboundCallCommand,
    Page,
    ProviderCall,
    ProviderEvent,
    RejectReason,
    RoutingDecision,
    TelephonyProvider,
    TranscriptSink,
)
from .responses import ResponsesAgentRuntime
from .router import (
    create_telephony_router,
    get_telephony,
    register_exception_handlers,
    set_telephony,
)
from .runner import LiveSessionRunner
from .runtime import build_runtime
from .session_graph import (
    InvalidLiveEvent,
    LiveDelegation,
    LiveSessionError,
    LiveSessionGraph,
    LiveSessionNode,
    LiveSessionState,
    LiveSessionUpdate,
)
from .tools import (
    ToolCallLimitExceeded,
    ToolError,
    ToolInvocationError,
    ToolNotAllowed,
    ToolNotFound,
    ToolRegistry,
    ToolSpec,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "TERMINAL_STATES",
    "AgentRequest",
    "AgentRuntime",
    "AgentUpdate",
    "AuthorizationPolicy",
    "Call",
    "CallContext",
    "CallDirection",
    "CallEventSink",
    "CallNotFound",
    "CallQuery",
    "CallRepository",
    "CallState",
    "CallTransition",
    "CallableAgentRuntime",
    "CommentaryUpdate",
    "DelegationTask",
    "EventInbox",
    "FakeProvider",
    "InMemoryCallRepository",
    "InMemoryEventInbox",
    "InMemoryLiveStateStore",
    "InboundRoutingPolicy",
    "InstructionUpdate",
    "InvalidAgentUpdate",
    "InvalidLiveEvent",
    "InvalidNumber",
    "InvalidTransition",
    "LiveGateway",
    "LiveCallController",
    "LiveIncomingCall",
    "LiveDelegation",
    "DelegationExecutor",
    "LiveFinalizationResult",
    "LiveSessionError",
    "LiveSessionGraph",
    "LiveSessionCoordinator",
    "LiveSessionNode",
    "LiveSessionRunner",
    "LangChainAgentRuntime",
    "LangGraphAgentRuntime",
    "ResponsesAgentRuntime",
    "build_runtime",
    "ToolCallLimitExceeded",
    "ToolError",
    "ToolInvocationError",
    "ToolNotAllowed",
    "ToolNotFound",
    "ToolRegistry",
    "ToolSpec",
    "LiveSessionRunnerPort",
    "LiveSessionState",
    "LiveSessionUpdate",
    "LiveStateStore",
    "OutboundCallCommand",
    "OpenAILiveConnection",
    "OpenAILiveGateway",
    "OpenAILiveSessionBuilder",
    "OpenAILiveTelephony",
    "Page",
    "PolicyDenied",
    "ProviderCall",
    "ProviderEvent",
    "RejectReason",
    "RoutingDecision",
    "TaskCancelled",
    "TaskCompleted",
    "TaskFailed",
    "Telephony",
    "TelephonyError",
    "TelephonyProvider",
    "TelephonySettings",
    "ThinkingUpdate",
    "TranscriptFragment",
    "TranscriptSink",
    "Usage",
    "WebhookVerificationError",
    "check_transfer_target",
    "create_telephony_router",
    "get_settings",
    "get_telephony",
    "mask_number",
    "new_call",
    "next_state",
    "normalize_e164",
    "register_exception_handlers",
    "set_telephony",
    "to_live_event",
]

__version__ = "0.1.0"
