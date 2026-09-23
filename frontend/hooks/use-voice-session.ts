"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { requestTurn, type TurnResult } from "@/lib/voice-api";

export type VoicePhase = "idle" | "connecting" | "listening" | "processing" | "speaking";
const SILENCE_MS = 850;
const MAX_UTTERANCE_MS = 20_000;

export function useVoiceSession() {
  const [phase, setPhase] = useState<VoicePhase>("idle");
  const [error, setError] = useState<string | null>(null);
  const [turns, setTurns] = useState<TurnResult[]>([]);
  const [voiceDetected, setVoiceDetected] = useState(false);
  const level = useRef(0);
  const phaseRef = useRef<VoicePhase>("idle");
  const epoch = useRef(0);
  const sessionId = useRef<string | null>(null);
  const stream = useRef<MediaStream | null>(null);
  const context = useRef<AudioContext | null>(null);
  const source = useRef<MediaStreamAudioSourceNode | null>(null);
  const analyser = useRef<AnalyserNode | null>(null);
  const recorder = useRef<MediaRecorder | null>(null);
  const playback = useRef<AudioBufferSourceNode | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const controller = useRef<AbortController | null>(null);
  const resumeListening = useRef<() => void>(() => {});

  const changePhase = useCallback((value: VoicePhase) => { phaseRef.current = value; setPhase(value); }, []);
  const stop = useCallback(() => {
    epoch.current += 1;
    controller.current?.abort();
    controller.current = null;
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    if (recorder.current) {
      recorder.current.onstop = null;
      recorder.current.ondataavailable = null;
      if (recorder.current.state !== "inactive") recorder.current.stop();
    }
    recorder.current = null;
    if (playback.current) { playback.current.onended = null; playback.current.stop(); }
    playback.current = null;
    stream.current?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    stream.current = null;
    source.current?.disconnect();
    source.current = null;
    analyser.current?.disconnect();
    analyser.current = null;
    if (context.current && context.current.state !== "closed") void context.current.close().catch(() => {});
    context.current = null;
    level.current = 0;
    setVoiceDetected(false);
    changePhase("idle");
  }, [changePhase]);

  useEffect(() => stop, [stop]);

  const send = useCallback(async (input: Blob | string, run: number) => {
    changePhase("processing");
    level.current = 0;
    setVoiceDetected(false);
    setError(null);
    const abort = new AbortController();
    controller.current = abort;
    const timeout = setTimeout(() => abort.abort(), 90_000);
    try {
      sessionId.current ??= crypto.randomUUID();
      const result = await requestTurn(input, sessionId.current, abort.signal);
      if (epoch.current !== run) return;
      setTurns((previous) => [...previous, result].slice(-10));
      if (result.audio_base64 && context.current) {
        const bytes = Uint8Array.from(atob(result.audio_base64), (character) => character.charCodeAt(0));
        const buffer = await context.current.decodeAudioData(bytes.buffer);
        if (epoch.current !== run || !context.current) return;
        const node = context.current.createBufferSource();
        node.buffer = buffer;
        node.connect(context.current.destination);
        playback.current = node;
        changePhase("speaking");
        node.onended = () => {
          playback.current = null;
          if (epoch.current === run) {
            if (result.action === "handoff") stop();
            else resumeListening.current();
          }
        };
        node.start();
      } else if (result.action === "handoff") stop();
      else resumeListening.current();
    } catch (cause) {
      if (epoch.current !== run) return;
      const message = abort.signal.aborted ? "The response timed out. Please try again." : cause instanceof Error ? cause.message : "Could not reach the voice service.";
      stop();
      setError(message);
    } finally {
      clearTimeout(timeout);
      if (controller.current === abort) controller.current = null;
    }
  }, [changePhase, stop]);

  const start = useCallback(async () => {
    if (phaseRef.current !== "idle") return;
    const run = ++epoch.current;
    changePhase("connecting");
    setError(null);
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("Microphone access requires HTTPS or localhost.");
      if (!window.MediaRecorder) throw new Error("Voice recording is unavailable in this browser. Use text below.");
      context.current = new AudioContext();
      await context.current.resume();
      if (epoch.current !== run) return;
      const microphone = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (epoch.current !== run) { microphone.getTracks().forEach((track) => track.stop()); return; }
      stream.current = microphone;
      microphone.getAudioTracks().forEach((track) => { track.onended = () => { stop(); setError("Microphone disconnected. Start again to reconnect."); }; });
      const audioContext = context.current!;
      source.current = audioContext.createMediaStreamSource(microphone);
      analyser.current = audioContext.createAnalyser();
      analyser.current.fftSize = 1024;
      source.current.connect(analyser.current);
      const samples = new Float32Array(analyser.current.fftSize);
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/ogg;codecs=opus"].find((type) => MediaRecorder.isTypeSupported(type));
      if (!mime) throw new Error("No supported recording format. Please use Chrome, Edge or Safari, or type below.");
      let voiceMs = 0;
      let lastVoice = 0;
      let beganAt = 0;
      const begin = () => {
        if (epoch.current !== run || !stream.current) return;
        voiceMs = 0;
        lastVoice = 0;
        beganAt = performance.now();
        const capture = new MediaRecorder(microphone, { mimeType: mime });
        recorder.current = capture;
        const chunks: Blob[] = [];
        capture.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
        capture.onerror = () => { if (epoch.current === run) { stop(); setError("Recording failed. Please reconnect your microphone."); } };
        capture.onstop = () => {
          if (epoch.current !== run) return;
          const blob = new Blob(chunks, { type: capture.mimeType });
          if (voiceMs >= 200 && blob.size) void send(blob, run);
          else begin();
        };
        capture.start(250);
        changePhase("listening");
      };
      resumeListening.current = begin;
      begin();
      timer.current = setInterval(() => {
        if (phaseRef.current !== "listening" || !analyser.current) { level.current = 0; return; }
        analyser.current.getFloatTimeDomainData(samples);
        const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
        const active = rms > 0.018;
        const now = performance.now();
        level.current = active ? Math.min(1, rms * 8) : 0;
        setVoiceDetected(active);
        if (active) { lastVoice = now; voiceMs += 50; }
        const silence = lastVoice > 0 && now - lastVoice > SILENCE_MS;
        if (silence || now - beganAt >= MAX_UTTERANCE_MS) {
          changePhase("processing");
          level.current = 0;
          setVoiceDetected(false);
          if (recorder.current?.state === "recording") recorder.current.stop();
        }
      }, 50);
    } catch (cause) {
      if (epoch.current !== run) return;
      stop();
      setError(cause instanceof DOMException && cause.name === "NotAllowedError" ? "Microphone permission was denied. Allow access or use text below." : cause instanceof Error ? cause.message : "Unable to start microphone.");
    }
  }, [changePhase, send, stop]);

  const sendText = useCallback(async (text: string) => {
    if (!text.trim() || phaseRef.current !== "idle") return;
    const run = ++epoch.current;
    changePhase("connecting");
    setError(null);
    try {
      context.current = new AudioContext();
      await context.current.resume();
      if (epoch.current !== run) return;
      resumeListening.current = stop;
      await send(text.trim(), run);
    } catch (cause) {
      if (epoch.current === run) { stop(); setError(cause instanceof Error ? cause.message : "Audio playback unavailable."); }
    }
  }, [changePhase, send, stop]);

  const reset = useCallback(() => { stop(); sessionId.current = null; setTurns([]); setError(null); }, [stop]);
  return { phase, error, turns, level, voiceDetected, start, stop, sendText, reset };
}
