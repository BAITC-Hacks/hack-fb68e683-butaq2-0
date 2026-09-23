from __future__ import annotations

from uuid import uuid4

from telephony.agents import AgentRequest, CommentaryUpdate
from telephony.domain import CallState, DelegationTask
from telephony.langchain import LangChainAgentRuntime
from telephony.langgraph import LangGraphAgentRuntime
from telephony.ports import CallContext


def _request(delegation_id: str, call_id):
    return AgentRequest(
        task=DelegationTask(
            delegation_id=delegation_id, call_id=call_id, instructions="Where is order A-1?"
        )
    )


class FakeRunnable:
    """A react-agent-shaped runnable: prompt, tool result, then the answer."""

    def __init__(self, output):
        self._output = output

    async def astream_events(self, _input, *, version):
        yield {"event": "on_chain_end", "data": {"output": self._output}, "parent_ids": []}


async def test_langchain_speaks_the_answer_not_the_tool_payload() -> None:
    runtime = LangChainAgentRuntime(
        FakeRunnable(
            {
                "messages": [
                    {"role": "user", "content": "Backend task: Where is order A-1?"},
                    # Responses API delivers the answer as content blocks.
                    {"role": "ai", "content": [{"type": "text", "text": "Order A-1 has shipped."}]},
                    {"role": "tool", "content": '{"result": {"status": "shipped"}}'},
                ]
            }
        )
    )
    call_id = uuid4()
    context = CallContext(call_id=call_id, scenario=None, state=CallState.CONNECTED)

    updates = [update async for update in runtime.run(_request("d1", call_id), context)]
    spoken = [u.text for u in updates if isinstance(u, CommentaryUpdate)]

    assert spoken == ["Order A-1 has shipped."]


def test_langgraph_threads_are_per_delegation() -> None:
    """checkpoint_ns does not fork a thread, so the delegation id must be in it."""

    call_id = uuid4()
    context = CallContext(call_id=call_id, scenario=None, state=CallState.CONNECTED)

    first = LangGraphAgentRuntime.config_for(_request("d1", call_id), context)
    second = LangGraphAgentRuntime.config_for(_request("d2", call_id), context)

    assert first["configurable"]["thread_id"] == f"{call_id}/d1"
    assert first["configurable"] != second["configurable"]
    assert "checkpoint_ns" not in first["configurable"]
