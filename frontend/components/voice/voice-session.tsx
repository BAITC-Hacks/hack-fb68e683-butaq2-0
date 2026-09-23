"use client";

import { useState } from "react";
import { Mic, MicOff, Send, RotateCcw } from "lucide-react";
import { VoicePoweredOrb } from "@/components/ui/voice-powered-orb";
import { Button } from "@/components/ui/button";
import { useVoiceSession } from "@/hooks/use-voice-session";
import { voiceCopy, type VoiceLocale } from "@/lib/voice-presentation";
import { VoiceTrace } from "./voice-trace";

export function VoiceSession() {
  const call = useVoiceSession();
  const [text, setText] = useState("");
  const [locale, setLocale] = useState<VoiceLocale>("ru");
  const copy = voiceCopy[locale];
  const active = call.phase !== "idle";
  const responding = call.phase === "speaking" || call.phase === "processing";
  const canSend = Boolean(text.trim()) && !call.sendingText && call.phase !== "connecting" && (!active || call.nativeVoice);
  async function submit() {
    if (!canSend) return;
    const draft = text;
    if (await call.sendText(draft)) setText((latest) => latest === draft ? "" : latest);
  }
  return <section id="voice" lang={locale} aria-label={copy.start} className="mt-6 w-full max-w-4xl scroll-mt-8">
    <div className="flex justify-center gap-2" role="group" aria-label="Язык интерфейса / Интерфейс тілі">{(["ru", "kk"] as const).map((value) => <Button key={value} variant={locale === value ? "default" : "outline"} aria-pressed={locale === value} onClick={() => setLocale(value)}>{value === "ru" ? "Русский" : "Қазақша"}</Button>)}</div>
    <div className="mx-auto h-56 w-56 sm:h-64 sm:w-64"><VoicePoweredOrb enableVoiceControl={active} audioLevelRef={call.level} /></div>
    <p role="status" className="mb-5 min-h-6 text-sm text-white/80">{call.microphoneMuted && call.phase === "listening" ? copy.muted : copy[call.phase]}</p>
    <div className="flex flex-wrap justify-center gap-3">
      <Button size="lg" variant={active ? "destructive" : "default"} className="gap-2 rounded-full px-8" onClick={() => active ? call.stop() : void call.start()}>{active ? <MicOff className="h-4 w-4" /> : <Mic className="h-4 w-4" />}{active ? copy.end : copy.start}</Button>
      {active && <Button variant="outline" size="lg" className="min-w-28 rounded-full" disabled={!responding} onClick={call.interrupt}>{copy.interrupt}</Button>}
      {active && call.nativeVoice && <Button variant="outline" size="lg" className="rounded-full" disabled={call.phase === "connecting"} aria-pressed={call.microphoneMuted} onClick={call.toggleMicrophone}>{call.microphoneMuted ? copy.unmute : copy.mute}</Button>}
      {(call.turns.length > 0 || call.liveCaptions.length > 0) && <Button variant="outline" size="lg" className="gap-2 rounded-full" onClick={call.reset}><RotateCcw className="h-4 w-4" />{copy.reset}</Button>}
    </div>
    <p className="mx-auto mt-4 max-w-lg text-xs leading-relaxed text-white/60">{copy.hint}</p>
    {call.liveCaptions.length > 0 && <div aria-label={copy.captions} className="mx-auto mt-4 max-h-60 max-w-lg overflow-auto text-left text-sm">{call.liveCaptions.map((caption, index) => <p key={index} className="mb-2 break-words text-white/80"><span className="font-semibold">{caption.speaker === "assistant" ? "Butaq" : copy.you} · </span>{caption.text}</p>)}</div>}
    {call.routingDecision && <p role="status" className="mx-auto mt-3 max-w-lg text-xs text-violet-200">{call.routingDecision.scenario_id ?? call.routingDecision.action} · {call.routingDecision.reason}</p>}
    {call.error && <p role="alert" className="mx-auto mt-4 max-w-lg rounded-xl border border-red-300/30 bg-black/80 p-4 text-sm text-red-200">{call.error}</p>}
    <form className="mx-auto mt-6 flex max-w-lg gap-2" onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <label htmlFor="voice-text" className="sr-only">{copy.input}</label>
      <input id="voice-text" value={text} onChange={(event) => setText(event.target.value)} maxLength={2000} placeholder={copy.input} aria-describedby="voice-text-count" className="min-w-0 flex-1 rounded-full border border-white/25 bg-black/50 px-5 py-3 text-sm text-white placeholder:text-white/50" />
      <Button type="submit" size="icon" aria-label={call.sendingText ? copy.sending : copy.send} disabled={!canSend} className="h-12 w-12 rounded-full"><Send className="h-4 w-4" /></Button>
    </form>
    <p id="voice-text-count" className="mx-auto mt-2 max-w-lg text-right text-xs text-white/50">{call.sendingText ? copy.sending : `${text.length}/2000`}</p>
    {call.error && !active && <Button variant="outline" className="mt-3 rounded-full" disabled={!text.trim()} onClick={() => void call.sendTextFallback(text)}>{copy.fallback}</Button>}
    <VoiceTrace turns={call.turns} locale={locale} />
  </section>;
}
