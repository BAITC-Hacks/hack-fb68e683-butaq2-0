"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { TurnResult } from "@/lib/voice-api";
import { exportTrace, measuredTimings, voiceCopy, type VoiceLocale } from "@/lib/voice-presentation";

export function VoiceTrace({ turns, locale }: { turns: TurnResult[]; locale: VoiceLocale }) {
  const [selected, setSelected] = useState<string | null>(null);
  const copy = voiceCopy[locale];
  const key = (turn: TurnResult) => turn.turn_id ?? turn.trace_id ?? `${turn.session_id}:${turn.transcript}`;
  const current = turns.find((turn) => key(turn) === selected) ?? turns.at(-1);
  if (!current) return null;
  const labels: Record<string, string> = locale === "ru"
    ? { stt_ms: "Распознавание", routing_ms: "Выбор сценария", response_ms: "Подготовка ответа", tts_ms: "Синтез", total_ms: "Backend (не полная задержка)", speech_end_to_audio_ms: "Конец речи → начало звука", playback_ms: "До воспроизведения после отправки", first_text_ms: "До текста ответа", first_audio_ms: "До аудио сервера" }
    : { stt_ms: "Сөйлеуді тану", routing_ms: "Сценарий таңдау", response_ms: "Жауап дайындау", tts_ms: "Синтез", total_ms: "Backend уақыты", speech_end_to_audio_ms: "Сөйлеу соңы → жауап дыбысы", playback_ms: "Жіберуден дыбысқа дейін", first_text_ms: "Жауап мәтініне дейін", first_audio_ms: "Сервер аудиосына дейін" };
  function download() {
    const url = URL.createObjectURL(new Blob([exportTrace(turns)], { type: "application/json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "butaq-trace.json";
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  if (current.playback_estimated) labels.playback_ms += locale === "ru" ? " (оценка)" : " (бағалау)";
  return <div className="mt-8 text-left">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-3"><h2>{copy.history}</h2><Button variant="outline" onClick={download}>{copy.export}</Button></div>
    <p className="mb-4 text-xs text-white/60">{copy.exportHint}</p>
    <div className="grid min-w-0 gap-4 md:grid-cols-2">
      <div className="max-h-[36rem] space-y-3 overflow-auto rounded-2xl border border-white/15 bg-black/80 p-4" aria-label={copy.history}>
        {turns.map((turn, index) => <button key={key(turn)} type="button" aria-pressed={key(turn) === key(current)} onClick={() => setSelected(key(turn))} className="block w-full break-words rounded-xl border border-white/15 p-4 text-left text-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-violet-300 aria-pressed:border-violet-300">
          <span className="mb-2 block text-xs text-violet-200">{index + 1} · {turn.scenario_id ?? turn.action} · {turn.language ?? "—"}</span>
          <span className="block text-white/70">{copy.you} · {turn.transcript}</span>
          <span className="mt-2 block">{turn.transport === "live" ? copy.verified : "Butaq"} · {turn.reply}</span>
          {turn.playback_interrupted && <span className="mt-2 block text-amber-200">{locale === "ru" ? "Ответ прерван" : "Жауап үзілді"}</span>}
        </button>)}
      </div>
      <section className="min-w-0 break-words rounded-2xl border border-white/15 bg-black/80 p-5 text-sm" aria-label={copy.trace}>
        <h2 className="mb-3 text-white/60">{copy.trace} · {current.action}</h2>
        <p>{current.scenario_id} · {current.scenario_title ?? current.action}</p>
        <p className="mt-2 text-xs text-violet-200">{Math.round(current.confidence * 100)}% · {copy.confidence}</p>
        <p className="mt-3">{current.reason}</p>
        <p className="mt-3 text-white/60">{copy.language}: {current.language ?? "—"} · {copy.transition}: {current.topic_transition ?? "—"}</p>
        {current.action === "handoff" && <p className="mt-3 text-amber-200">{copy.handoff}</p>}
        <h3 className="mt-4 text-white/60">{copy.alternatives}</h3>
        {current.alternatives.length ? current.alternatives.map((item) => <p key={item.scenario_id} className="mt-2">{item.scenario_id}: {item.reason}</p>) : <p>{copy.none}</p>}
        <h3 className="mt-4 text-white/60">{copy.parameters}</h3>
        {current.extracted_parameters?.length ? current.extracted_parameters.map((item) => <p key={`${item.scenario_id}:${item.name}`}>{item.scenario_id} · {item.name}: {item.value}</p>) : <p>{copy.none}</p>}
        <p className="mt-4">{copy.pending}: {current.pending_scenario_ids.join(", ") || copy.none}</p>
        <dl className="mt-4 grid grid-cols-2 gap-3 border-t border-white/15 pt-4 text-xs">{Object.entries(measuredTimings(current)).map(([name, value]) => <div key={name}><dt className="text-white/60">{labels[name] ?? name}</dt><dd className="mt-1 tabular-nums">{value === null ? copy.unmeasured : `${Math.round(value)} ms`}</dd></div>)}</dl>
        <p className="mt-4 text-xs text-white/50">{copy.readonly}</p>
        <p className="mt-2 break-all text-xs text-white/40">{current.trace_id}</p>
      </section>
    </div>
  </div>;
}
