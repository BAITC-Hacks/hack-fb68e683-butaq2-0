"""Translate Live transcript snapshots into the same turns used by the browser."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID

from multi_agent.contracts import TurnResult
from pydantic import BaseModel, Field
from telephony.agents import AgentRequest, AgentUpdate, CommentaryUpdate, TaskCompleted
from telephony.ports import CallContext

from app.services.voice_router import RouterService

from .settings import PhoneSettings


class PhoneTurnTrace(BaseModel):
    delegation_id: str
    status: str
    transcript: str = ""
    result: TurnResult | None = None
    error: str | None = None


class PhoneTrace(BaseModel):
    call_id: UUID
    conversation_id: str
    state: str = "accepted"
    turns: list[PhoneTurnTrace] = Field(default_factory=list)
    # Live has no separate STT/TTS request here; these timings are not measured.
    speech_end_to_audio_ms: float | None = None


@dataclass
class _Dialogue:
    trace: PhoneTrace
    processed: set[tuple[int, int, str]] = field(default_factory=set)
    delegations: set[str] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    closed: bool = False
    ended_at: float | None = None


class ButaqPhoneRuntime:
    def __init__(self, service: RouterService, settings: PhoneSettings):
        self.service = service
        self.settings = settings
        self.dialogues: dict[UUID, _Dialogue] = {}

    @staticmethod
    def conversation_id(call_id: UUID) -> str:
        return f"phone:{call_id}"

    def open(self, call_id: UUID) -> None:
        self.dialogues.setdefault(call_id, _Dialogue(PhoneTrace(call_id=call_id, conversation_id=self.conversation_id(call_id))))

    def finish(self, call_id: UUID, state: str) -> None:
        dialogue = self.dialogues.get(call_id)
        if dialogue:
            dialogue.closed = True
            dialogue.trace.state = state
            dialogue.ended_at = dialogue.ended_at or monotonic()
            dialogue.processed.clear()
            dialogue.delegations.clear()
        # Callers must stop the runner before dropping shared routing state.
        self.service.sessions.pop(self.conversation_id(call_id), None)

    def forget(self, call_id: UUID) -> None:
        self.finish(call_id, "expired")
        self.dialogues.pop(call_id, None)

    async def run(self, request: AgentRequest, context: CallContext) -> AsyncIterator[AgentUpdate]:
        dialogue = self.dialogues.get(context.call_id)
        if dialogue is None or dialogue.closed:
            yield TaskCompleted()
            return
        async with dialogue.lock:
            delegation_id = request.task.delegation_id
            if dialogue.closed or delegation_id in dialogue.delegations:
                yield TaskCompleted()
                return
            offset = request.task.arguments.get("offset_ms", 0)
            fragments = sorted({(f.start_ms, f.end_ms, f.text) for f in context.transcript
                                if f.speaker == "user" and f.end_ms <= offset} - dialogue.processed)
            if not fragments:
                # Do not fabricate a user turn from the delegation ID or assistant speech.
                if not dialogue.processed:
                    yield CommentaryUpdate("Не удалось разобрать запрос. Пожалуйста, повторите. / Сұрағыңызды қайталаңызшы.")
                yield TaskCompleted()
                return
            if len(dialogue.trace.turns) >= self.settings.max_turns_per_call:
                yield CommentaryUpdate("Достигнут лимит диалога. Пожалуйста, завершите звонок. / Диалог шегіне жеттік. Қоңырауды аяқтаңызшы.")
                yield TaskCompleted()
                return
            text = "".join(part[2] for part in fragments).strip()
            try:
                result = await asyncio.wait_for(self.service.turn(session_id=self.conversation_id(context.call_id), text=text, synthesize=False), self.settings.delegation_timeout_seconds)
                if dialogue.closed:
                    yield TaskCompleted()
                    return
                if result.action == "handoff":
                    result.reply = ("Бұл мәселені сенімді шеше алмаймын. Маманға қосу әзірге қолжетімсіз."
                                    if result.language == "kk" else
                                    "Я не могу надёжно решить этот вопрос. Подключение специалиста пока недоступно.")
                    state = self.service.sessions.get(result.session_id)
                    if state and state.history and state.history[-1]["role"] == "assistant":
                        state.history[-1]["content"] = result.reply
                # No phone media is sent through VoicePipeline.speak_bytes().
                dialogue.trace.turns.append(PhoneTurnTrace(delegation_id=delegation_id, status="completed", transcript=text, result=result))
                reply = result.reply
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - transport boundary; never disclose provider errors
                # Never speak raw provider errors or credentials. Caller may retry with new speech.
                dialogue.trace.turns.append(PhoneTurnTrace(delegation_id=delegation_id, status="failed", transcript=text, error="routing_unavailable"))
                reply = "Не удалось обработать запрос. Повторите, пожалуйста. / Сұрауды өңдеу мүмкін болмады. Қайталаңызшы."
            dialogue.processed.update(fragments)
            dialogue.delegations.add(delegation_id)
            yield CommentaryUpdate(reply)
            yield TaskCompleted()
