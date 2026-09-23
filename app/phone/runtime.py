"""Translate Live transcript snapshots into the same turns used by the browser."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from time import monotonic
from uuid import UUID

from multi_agent.contracts import TurnResult
from multi_agent.live import commentary_chunks
from pydantic import BaseModel, Field
from telephony.agents import AgentRequest, AgentUpdate, CommentaryUpdate, TaskCompleted
from telephony.domain import TranscriptFragment
from telephony.ports import CallContext

from app.services.voice_router import RouterService

from .settings import PhoneSettings


class PhoneTurnTrace(BaseModel):
    delegation_id: str
    # Status describes backend execution, never confirmed caller playback.
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


def _transcript_at(context: CallContext, offset: int) -> list[TranscriptFragment]:
    """Keep provider order for equal timestamps; deduplicate replayed fragments.

    An interval can straddle the delegation timestamp. Its text was already
    observed, so include it just as the native browser adapter does. This cannot
    recover fragments absent from the coordinator's snapshot.
    """
    seen: set[tuple[str, int, int, str]] = set()
    fragments = []
    for part in sorted(
        context.transcript, key=lambda item: (item.start_ms, item.end_ms)
    ):
        identity = (part.speaker, part.start_ms, part.end_ms, part.text)
        if part.start_ms <= offset and identity not in seen:
            fragments.append(part)
            seen.add(identity)
    return fragments


def _voice_context(fragments: list[TranscriptFragment]) -> list[dict[str, str]]:
    """Observed Live captions are useful context, not proof of delivered speech."""
    messages: list[dict[str, str]] = []
    for part in fragments:
        if part.speaker not in {"user", "assistant"}:
            continue
        if messages and messages[-1]["role"] == part.speaker:
            messages[-1]["content"] = (messages[-1]["content"] + part.text)[-2000:]
        else:
            messages.append({"role": part.speaker, "content": part.text[-2000:]})
            messages = messages[-20:]
    return messages


class ButaqPhoneRuntime:
    def __init__(self, service: RouterService, settings: PhoneSettings):
        self.service = service
        self.settings = settings
        self.dialogues: dict[UUID, _Dialogue] = {}

    @staticmethod
    def conversation_id(call_id: UUID) -> str:
        return f"phone:{call_id}"

    def open(self, call_id: UUID) -> None:
        self.dialogues.setdefault(
            call_id,
            _Dialogue(
                PhoneTrace(
                    call_id=call_id, conversation_id=self.conversation_id(call_id)
                )
            ),
        )

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

    async def run(
        self, request: AgentRequest, context: CallContext
    ) -> AsyncIterator[AgentUpdate]:
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
            observed = _transcript_at(context, offset)
            fragments = [
                (part.start_ms, part.end_ms, part.text)
                for part in observed
                if part.speaker == "user"
                and (part.start_ms, part.end_ms, part.text) not in dialogue.processed
            ]
            if not fragments:
                # Never fabricate a user turn from delegation metadata or assistant speech.
                if not dialogue.processed:
                    yield CommentaryUpdate(
                        "Не удалось разобрать запрос. Пожалуйста, повторите. / "
                        "Сұрағыңызды қайталаңызшы."
                    )
                yield TaskCompleted()
                return
            if len(dialogue.trace.turns) >= self.settings.max_turns_per_call:
                yield CommentaryUpdate(
                    "Достигнут лимит диалога. Пожалуйста, завершите звонок. / "
                    "Диалог шегіне жеттік. Қоңырауды аяқтаңызшы."
                )
                yield TaskCompleted()
                return
            text = "".join(part[2] for part in fragments).strip()
            if not text:
                yield TaskCompleted()
                return
            session_id = self.conversation_id(context.call_id)
            try:
                result = await asyncio.wait_for(
                    self.service.turn(
                        session_id=session_id,
                        text=text,
                        synthesize=False,
                        delivery_id=delegation_id,
                        voice_context=_voice_context(observed),
                    ),
                    self.settings.delegation_timeout_seconds,
                )
                if dialogue.closed:
                    return
                if result.action == "handoff":
                    result.reply = (
                        "Бұл мәселені сенімді шеше алмаймын. "
                        "Маманға қосу әзірге қолжетімсіз."
                        if result.language == "kk"
                        else "Я не могу надёжно решить этот вопрос. "
                        "Подключение специалиста пока недоступно."
                    )
                chunks = commentary_chunks(result.reply)
                # No phone media is sent through VoicePipeline.speak_bytes().
                dialogue.trace.turns.append(
                    PhoneTurnTrace(
                        delegation_id=delegation_id,
                        status="completed",
                        transcript=text,
                        result=result,
                    )
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - never disclose provider errors
                dialogue.trace.turns.append(
                    PhoneTurnTrace(
                        delegation_id=delegation_id,
                        status="failed",
                        transcript=text,
                        error="routing_unavailable",
                    )
                )
                chunks = commentary_chunks(
                    "Не удалось обработать запрос. Повторите, пожалуйста. / "
                    "Сұрауды өңдеу мүмкін болмады. Қайталаңызшы."
                )
            finally:
                # SIP/Live provides no per-answer playback acknowledgement. Keep
                # accepted user facts, but never persist an unheard backend draft.
                await self.service.orchestrator.interrupt_delivery(
                    session_id, delegation_id
                )
            dialogue.processed.update(fragments)
            dialogue.delegations.add(delegation_id)
            for chunk in chunks:
                yield CommentaryUpdate(chunk)
            yield TaskCompleted()
