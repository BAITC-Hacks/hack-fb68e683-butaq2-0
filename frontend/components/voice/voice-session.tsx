"use client";

import { useState } from "react";
import { Mic, MicOff, Send, RotateCcw } from "lucide-react";
import { VoicePoweredOrb } from "@/components/ui/voice-powered-orb";
import { Button } from "@/components/ui/button";
import { useVoiceSession } from "@/hooks/use-voice-session";

export function VoiceSession() {
  const call = useVoiceSession();
  const [text, setText] = useState("");
  const current = call.turns.at(-1);
  const active = call.phase !== "idle";
  const label = { idle: "Ready when you are", connecting: "Connecting microphone…", listening: call.voiceDetected ? "I can hear you" : "Listening — take your time", processing: "Finding the right response…", speaking: "Butaq is speaking — you can interrupt" }[call.phase];

  return (
    <section id="voice" aria-label="Talk to Butaq" className="mt-10 w-full max-w-4xl scroll-mt-8">
      <div className="mx-auto h-64 w-64 sm:h-80 sm:w-80">
        <VoicePoweredOrb enableVoiceControl={active} audioLevelRef={call.level} />
      </div>
      <p role="status" className="mb-5 text-sm text-white/80">{label}</p>
      <div className="flex flex-wrap justify-center gap-3">
        <Button size="lg" variant={active ? "destructive" : "default"} className="gap-2 rounded-full px-8" onClick={() => active ? call.stop() : void call.start()}>
          {active ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}
          {active ? "End conversation" : "Talk to Butaq"}
        </Button>
        {!call.nativeVoice && call.phase === "listening" && call.voiceDetected && <Button variant="outline" size="lg" className="rounded-full" onClick={call.commit}>Send now</Button>}
        {(call.phase === "speaking" || call.phase === "processing") && <Button variant="outline" size="lg" className="rounded-full" onClick={call.interrupt}>Interrupt</Button>}
        {call.turns.length > 0 && <Button variant="outline" size="lg" className="gap-2 rounded-full" onClick={call.reset}><RotateCcw className="h-4 w-4" />New conversation</Button>}
      </div>
      <p className="mx-auto mt-4 max-w-md text-xs leading-relaxed text-white/60">Speak Russian or Kazakh. Talk naturally; your microphone stays connected. You can speak over Butaq to interrupt; the microphone stays on. Headphones help in noisy rooms.</p>
      {call.transcript && <p role="status" className="mx-auto mt-4 max-w-lg text-sm text-white/80">You · {call.transcript}</p>}
      {call.liveCaptions.length > 0 && <div aria-label="Live voice captions" aria-live="polite" className="mx-auto mt-4 max-h-60 max-w-lg overflow-auto text-left text-sm">
        {call.liveCaptions.map((caption, index) => <p key={index} className="mb-2 text-white/80"><span className={caption.speaker === "assistant" ? "font-semibold text-violet-300" : "font-semibold"}>{caption.speaker === "assistant" ? "Butaq" : "You"} · </span>{caption.text}</p>)}
      </div>}
      {call.routingDecision && <p role="status" className="mx-auto mt-3 max-w-lg text-xs text-violet-200">{call.routingDecision.scenario_id ? `Selected ${call.routingDecision.scenario_id}` : call.routingDecision.action} · {Math.round(call.routingDecision.confidence * 100)}% · {call.routingDecision.reason}</p>}
      {call.error && <p role="alert" className="mx-auto mt-4 max-w-lg rounded-xl border border-red-300/30 bg-black/80 p-4 text-sm text-red-200">{call.error}</p>}
      <form className="mx-auto mt-6 flex max-w-lg gap-2" onSubmit={(event) => { event.preventDefault(); if (text.trim() && !active) { void call.sendText(text); setText(""); } }}>
        <label htmlFor="voice-text" className="sr-only">Type your message</label>
        <input id="voice-text" value={text} onChange={(event) => setText(event.target.value)} disabled={active} maxLength={8000} placeholder="Or type your message…" className="min-w-0 flex-1 rounded-full border border-white/25 bg-black/50 px-5 py-3 text-sm text-white placeholder:text-white/50 disabled:opacity-50" />
        <Button type="submit" size="icon" aria-label="Send message" disabled={active || !text.trim()} className="h-12 w-12 rounded-full"><Send className="h-4 w-4" /></Button>
      </form>
      {call.error && !active && <Button variant="outline" className="mt-3 rounded-full" disabled={!text.trim()} onClick={() => { if (text.trim()) { void call.sendTextFallback(text); setText(""); } }}>Send typed message without streaming</Button>}
      {current && (
        <div className="mt-8 grid gap-4 text-left md:grid-cols-2">
          <div className="max-h-96 overflow-auto rounded-2xl border border-white/15 bg-black/80 p-5" aria-label="Conversation transcript" aria-live="polite">
            <h2 className="mb-4 text-xs uppercase tracking-widest text-white/50">{call.nativeVoice ? "Verified backend answers" : "Conversation"}</h2>
            {call.turns.map((turn, index) => <div key={index} className="mb-5 space-y-2 text-sm leading-relaxed"><p className="text-white/60"><span className="font-semibold text-white">You · </span>{turn.transcript}</p><p><span className="font-semibold text-violet-300">{call.nativeVoice ? "Backend answer · " : "Butaq · "}</span>{turn.reply}{turn.playback_interrupted && <span className="ml-2 text-xs text-amber-200">(interrupted)</span>}</p></div>)}
          </div>
          <div className="rounded-2xl border border-white/15 bg-black/80 p-5 text-sm" aria-label="Routing trace">
            <h2 className="mb-3 text-xs uppercase tracking-widest text-white/50">Latest decision · {current.action}</h2>
            <p className="font-medium">{current.scenario_title ?? (current.action === "handoff" ? "Operator requested" : "Clarification")}</p>
            <p className="mt-1 text-xs text-violet-300">{current.scenario_id ?? "No scenario selected"} · {Math.round(current.confidence * 100)}% confidence</p>
            <p className="mt-3 leading-relaxed text-white/70">{current.reason}</p>
            {current.action === "handoff" && <p className="mt-3 text-amber-200">Human assistance is required. This screen does not connect to a live operator.</p>}
            <h3 className="mb-2 mt-4 text-xs uppercase tracking-widest text-white/50">Alternatives</h3>
            {current.alternatives.length ? current.alternatives.map((item, index) => <p key={index} className="mb-2 text-xs text-white/70">{item.scenario_id}: {item.reason}</p>) : <p className="text-xs text-white/50">None returned</p>}
            {current.pending_scenario_ids.length > 0 && <p className="mt-3 text-xs text-white/60">Pending: {current.pending_scenario_ids.join(", ")}</p>}
            <dl className="mt-4 grid grid-cols-2 gap-2 border-t border-white/15 pt-4 text-xs">
              {Object.entries(current.timings).map(([key, value]) => <div key={key}><dt className="text-white/50">{{ stt_ms: "Recognition", routing_ms: "Routing", response_ms: "Response", tts_ms: "Voice synthesis", total_ms: "Server total", first_audio_ms: "First audio from server", first_text_ms: "First reply text", playback_ms: current.playback_estimated ? "First audio playback (estimated)" : "First audio playback" }[key] ?? key}</dt><dd className="mt-1 tabular-nums">{Math.round(value ?? 0)} ms</dd></div>)}
            </dl>
          </div>
        </div>
      )}
    </section>
  );
}
