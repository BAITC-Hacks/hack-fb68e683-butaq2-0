"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { requestTurn, type TurnResult, type LiveRoutingDecision } from "@/lib/voice-api";
import { VoiceStream, type VoicePhase } from "@/lib/voice-stream";
import { VoiceLive, type LiveCaption } from "@/lib/voice-live";

export type { VoicePhase } from "@/lib/voice-stream";

export function useVoiceSession() {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [turns, setTurns] = useState<TurnResult[]>([]);
  const [voiceDetected, setVoiceDetected] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [liveCaptions, setLiveCaptions] = useState<LiveCaption[]>([]);
  const [nativeVoice, setNativeVoice] = useState(false);
  const [microphoneMuted, setMicrophoneMuted] = useState(false);
  const [sendingText, setSendingText] = useState(false);
  const [routingDecision, setRoutingDecision] = useState<LiveRoutingDecision | null>(null);
  const level = useRef(0);
  const phaseRef = useRef<VoicePhase>("idle");
  const epoch = useRef(0);
  const sessionId = useRef<string | null>(null);
  const connection = useRef<VoiceStream | VoiceLive | null>(null);
  const controller = useRef<AbortController | null>(null);
  const fallbackContext = useRef<AudioContext | null>(null);
  const fallbackPlayback = useRef<AudioBufferSourceNode | null>(null);

  const changePhase = useCallback((value: VoicePhase) => { phaseRef.current = value; setPhase(value); }, []);
  const stop = useCallback(() => {
    ++epoch.current;
    connection.current?.stop();
    connection.current = null;
    controller.current?.abort();
    controller.current = null;
    if (fallbackPlayback.current) {
      fallbackPlayback.current.onended = null;
      fallbackPlayback.current.stop();
      fallbackPlayback.current.disconnect();
      fallbackPlayback.current = null;
    }
    if (fallbackContext.current && fallbackContext.current.state !== "closed") void fallbackContext.current.close().catch(() => {});
    fallbackContext.current = null;
    level.current = 0;
    setVoiceDetected(false);
    setMicrophoneMuted(false);
    setTranscript("");
    setRoutingDecision(null);
    changePhase("idle");
  }, [changePhase]);

  useEffect(() => stop, [stop]);

  const open = useCallback(async (mode: "voice" | "text", text?: string) => {
    if (phaseRef.current !== "idle") return false;
    connection.current?.stop();
    const run = ++epoch.current;
    setError(null);
    setMicrophoneMuted(false);
    sessionId.current ??= crypto.randomUUID();
    setNativeVoice(mode === "voice");
    const callbacks = {
      phase: (value: VoicePhase) => { if (epoch.current === run) changePhase(value); },
      error: (message: string) => { if (epoch.current === run) setError(message); },
      route: (decision: LiveRoutingDecision | null) => { if (epoch.current === run) setRoutingDecision(decision); },
      transcript: (text: string) => { if (epoch.current === run) setTranscript(text); },
      level: (value: number, voiced: boolean) => {
        if (epoch.current !== run) return;
        level.current = value;
        setVoiceDetected(voiced);
      },
      result: (result: TurnResult) => {
        if (epoch.current !== run) return;
        result = { ...result, transport: mode === "voice" ? "live" : "stream" };
        setTurns((previous) => {
          const existing = previous.findIndex((turn) => turn.turn_id === result.turn_id);
          return (existing < 0 ? [...previous, result] : previous.map((turn, index) => index === existing ? result : turn)).slice(-10);
        });
      },
    };
    const call = mode === "voice" ? new VoiceLive(sessionId.current, {
      ...callbacks,
      caption: (caption) => {
        if (epoch.current !== run) return;
        setLiveCaptions((previous) => {
          const last = previous.at(-1);
          if (last?.speaker === caption.speaker && caption.start_ms >= last.start_ms && caption.start_ms - last.end_ms < 1500) {
            return [...previous.slice(0, -1), { ...last, text: last.text + caption.text, end_ms: caption.end_ms }];
          }
          return [...previous, caption].slice(-30);
        });
      },
    }) : new VoiceStream(sessionId.current, callbacks);
    connection.current = call;
    if (call instanceof VoiceLive) await call.start();
    else await call.start(mode, text);
    if (mode === "text" && call instanceof VoiceStream) return call.textAccepted();
    return phaseRef.current !== "idle";
  }, [changePhase]);

  const start = useCallback(() => open("voice"), [open]);
  // Typing during a live call rides the open connection; otherwise it opens a text turn.
  const sendText = useCallback(async (text: string) => {
    if (!text.trim() || text.trim().length > 2000 || phaseRef.current === "connecting") return false;
    setSendingText(true);
    setError(null);
    try {
      if (phaseRef.current === "idle") return await open("text", text.trim());
      if (connection.current instanceof VoiceLive) return await connection.current.sendText(text.trim());
      return false;
    } finally { setSendingText(false); }
  }, [open]);
  const toggleMicrophone = useCallback(() => {
    if (!(connection.current instanceof VoiceLive)) return;
    setMicrophoneMuted((previous) => { connection.current instanceof VoiceLive && connection.current.setMicrophoneMuted(!previous); return !previous; });
  }, []);
  const commit = useCallback(() => { if (connection.current instanceof VoiceStream) connection.current.commit(); }, []);
  const interrupt = useCallback(() => {
    if (connection.current) connection.current.interrupt();
    else stop();
  }, [stop]);

  // Explicit user action only. Never automatically resend a failed streamed turn.
  const sendTextFallback = useCallback(async (text: string) => {
    if (!text.trim() || phaseRef.current !== "idle") return;
    const run = ++epoch.current;
    connection.current?.stop();
    connection.current = null;
    const abort = new AbortController();
    controller.current = abort;
    const timeout = setTimeout(() => abort.abort(), 90_000);
    setError(null);
    changePhase("processing");
    try {
      sessionId.current ??= crypto.randomUUID();
      const context = new AudioContext();
      fallbackContext.current = context;
      await context.resume();
      if (epoch.current !== run) return;
      const result = await requestTurn(text.trim(), sessionId.current, abort.signal);
      if (epoch.current !== run) return;
      setTurns((previous) => [...previous, { ...result, transport: "http" as const }].slice(-10));
      if (!result.audio_base64) { stop(); return; }
      const bytes = Uint8Array.from(atob(result.audio_base64), (character) => character.charCodeAt(0));
      const buffer = await context.decodeAudioData(bytes.buffer);
      if (epoch.current !== run) return;
      const node = context.createBufferSource();
      node.buffer = buffer;
      node.connect(context.destination);
      fallbackPlayback.current = node;
      node.onended = () => { if (epoch.current === run) stop(); };
      changePhase("speaking");
      node.start();
    } catch (cause) {
      if (epoch.current !== run) return;
      stop();
      setError(abort.signal.aborted ? "The response timed out. Please try again." : cause instanceof Error ? cause.message : "Could not reach the voice service.");
    } finally {
      clearTimeout(timeout);
      if (controller.current === abort) controller.current = null;
    }
  }, [changePhase, stop]);

  const reset = useCallback(() => { stop(); sessionId.current = null; setTurns([]); setLiveCaptions([]); setError(null); }, [stop]);
  return { phase, error, turns, level, voiceDetected, transcript, liveCaptions, nativeVoice, microphoneMuted, sendingText, toggleMicrophone, routingDecision, start, stop, sendText, sendTextFallback, commit, interrupt, reset };
}
