import { PcmPlayback, SpeechGate, VOICE_SAMPLE_RATE, pcmToBase64 } from "./voice-audio";
import { normalizeTurnResult, routerUrl, type TurnResult, type LiveRoutingDecision } from "./voice-api";

export type VoicePhase = "idle" | "connecting" | "listening" | "processing" | "speaking";
type Mode = "voice" | "text";
interface Callbacks {
  phase: (phase: VoicePhase) => void;
  result: (result: TurnResult) => void;
  transcript: (text: string) => void;
  route: (decision: LiveRoutingDecision | null) => void;
  level: (level: number, voiced: boolean) => void;
  error: (message: string) => void;
}
interface ActiveTurn {
  id: string;
  committedAt: number;
  result?: TurnResult;
  playback: PcmPlayback;
  serverCompleted: boolean;
  firstTextMs?: number;
  playbackMs?: number;
  playbackEstimated?: boolean;
  sequence: number;
  transcript: string;
}
interface StreamEvent {
  type: string;
  turn_id?: string;
  seq?: number;
  sample_rate?: number;
  text?: string;
  delta?: string;
  audio?: string;
  message?: string;
  result?: TurnResult;
  decision?: LiveRoutingDecision;
  metrics?: { first_audio_ms?: number; tts_ms?: number; total_ms?: number };
}

export function voiceSocketUrl(): string {
  const url = new URL(routerUrl("stream"), window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return url.href;
}

/** One connection owns capture, generation and playback; old connections cannot update UI. */
export class VoiceStream {
  private epoch = 0;
  private socket: WebSocket | null = null;
  private context: AudioContext | null = null;
  private microphone: MediaStream | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private capture: AudioWorkletNode | null = null;
  private silent: GainNode | null = null;
  private gate: SpeechGate | null = null;
  private active: ActiveTurn | null = null;
  private mode: Mode = "voice";
  private ready = false;
  private connectReject: ((error: Error) => void) | null = null;
  private connectTimer: ReturnType<typeof setTimeout> | null = null;
  private responseTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(private readonly sessionId: string, private readonly callbacks: Callbacks) {}

  async start(mode: Mode, text?: string): Promise<void> {
    const epoch = ++this.epoch;
    this.mode = mode;
    this.callbacks.phase("connecting");
    try {
      this.context = new AudioContext();
      await this.context.resume();
      if (epoch !== this.epoch) return;
      await this.connect(epoch);
      if (epoch !== this.epoch) return;
      if (mode === "voice") await this.startMicrophone(epoch);
      else {
        const turn = this.beginTurn();
        turn.committedAt = performance.now();
        this.send({ type: "text", turn_id: turn.id, text });
        this.awaitResponse();
      }
    } catch (cause) {
      if (epoch !== this.epoch) return;
      this.fail(cause instanceof DOMException && cause.name === "NotAllowedError"
        ? "Microphone permission was denied. Allow access or type your message."
        : cause instanceof Error ? cause.message : "Unable to connect the voice service.");
    }
  }

  commit(): void {
    try { this.gate?.commit(); }
    catch (cause) { this.fail(cause instanceof Error ? cause.message : "Microphone streaming failed."); }
  }

  interrupt(): void {
    const turn = this.active;
    if (!turn) return;
    // Stop locally first: network cancellation must never delay barge-in.
    turn.playback.stop();
    this.active = null;
    this.clearResponseTimer();
    if (turn.result) this.callbacks.result({ ...turn.result, playback_interrupted: true });
    try { this.send({ type: "interrupt", turn_id: turn.id }); }
    catch (cause) { this.fail(cause instanceof Error ? cause.message : "Voice connection lost."); return; }
    this.callbacks.transcript("");
    this.callbacks.route(null);
    if (this.mode === "text") this.stop();
    else this.callbacks.phase("listening");
  }

  stop(): void {
    ++this.epoch;
    this.ready = false;
    this.connectReject?.(new Error("Conversation ended."));
    this.connectReject = null;
    if (this.connectTimer) clearTimeout(this.connectTimer);
    this.connectTimer = null;
    this.clearResponseTimer();
    this.active?.playback.stop();
    this.active = null;
    this.gate?.reset();
    this.gate = null;
    if (this.capture) {
      this.capture.port.onmessage = null;
      this.capture.onprocessorerror = null;
      this.capture.port.close();
      this.capture.disconnect();
    }
    this.capture = null;
    this.source?.disconnect();
    this.source = null;
    this.silent?.disconnect();
    this.silent = null;
    this.microphone?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    this.microphone = null;
    if (this.socket) {
      this.socket.onopen = this.socket.onmessage = this.socket.onclose = this.socket.onerror = null;
      this.socket.close();
    }
    this.socket = null;
    if (this.context && this.context.state !== "closed") void this.context.close().catch(() => {});
    this.context = null;
    this.callbacks.level(0, false);
    this.callbacks.transcript("");
    this.callbacks.route(null);
    this.callbacks.phase("idle");
  }

  private connect(epoch: number): Promise<void> {
    return new Promise((resolve, reject) => {
      const socket = new WebSocket(voiceSocketUrl());
      this.socket = socket;
      this.connectReject = reject;
      this.connectTimer = setTimeout(() => reject(new Error("Streaming connection timed out. Reconnect or use the text fallback.")), 20_000);
      socket.onopen = () => {
        if (epoch === this.epoch) this.send({ type: "start", session_id: this.sessionId, mode: this.mode });
      };
      socket.onmessage = (event) => {
        if (epoch !== this.epoch) return;
        try {
          const message: StreamEvent = JSON.parse(event.data);
          if (!message || typeof message.type !== "string") throw new Error("Invalid streaming response.");
          if (message.type === "ready") {
            if (message.sample_rate !== VOICE_SAMPLE_RATE) throw new Error("Unsupported microphone audio format.");
            this.ready = true;
            this.connectReject = null;
            if (this.connectTimer) clearTimeout(this.connectTimer);
            this.connectTimer = null;
            resolve();
          } else this.receive(message);
        } catch (cause) {
          this.fail(cause instanceof Error ? cause.message : "Invalid streaming response.");
        }
      };
      const disconnected = () => {
        if (epoch !== this.epoch) return;
        const message = "Voice connection lost. Reconnect, or use the text fallback. Your last turn was not resent.";
        if (this.ready) this.fail(message);
        else reject(new Error(message));
      };
      socket.onerror = disconnected;
      socket.onclose = disconnected;
    });
  }

  private async startMicrophone(epoch: number): Promise<void> {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("Microphone access requires HTTPS or localhost.");
    const context = this.context!;
    if (!context.audioWorklet) throw new Error("Streaming microphone is unavailable in this browser. Please type your message.");
    await context.audioWorklet.addModule("/voice-capture.worklet.js");
    if (epoch !== this.epoch) return;
    const microphone = await navigator.mediaDevices.getUserMedia({ audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
    if (epoch !== this.epoch) { microphone.getTracks().forEach((track) => track.stop()); return; }
    this.microphone = microphone;
    microphone.getAudioTracks().forEach((track) => { track.onended = () => this.fail("Microphone disconnected. Reconnect to continue."); });
    this.gate = new SpeechGate({
      start: () => this.beginTurn(),
      audio: (samples) => {
        if (this.active) this.send({ type: "audio.append", turn_id: this.active.id, audio: pcmToBase64(samples) });
      },
      end: (accepted) => {
        if (!this.active) return;
        if (!accepted) { this.interrupt(); return; }
        this.active.committedAt = performance.now();
        this.send({ type: "audio.commit", turn_id: this.active.id });
        this.awaitResponse();
      },
      level: (rms, voiced) => this.callbacks.level(Math.min(1, rms * 8), voiced),
    });
    this.source = context.createMediaStreamSource(microphone);
    this.capture = new AudioWorkletNode(context, "voice-capture", { channelCount: 1, channelCountMode: "explicit" });
    this.capture.port.onmessage = (event: MessageEvent<ArrayBuffer>) => {
      if (epoch === this.epoch && this.ready) {
        try { this.gate?.push(new Int16Array(event.data)); }
        catch (cause) { this.fail(cause instanceof Error ? cause.message : "Microphone streaming failed."); }
      }
    };
    this.capture.onprocessorerror = () => this.fail("Microphone processing failed. Reconnect to continue.");
    this.silent = context.createGain();
    this.silent.gain.value = 0;
    this.source.connect(this.capture);
    this.capture.connect(this.silent);
    this.silent.connect(context.destination);
    this.callbacks.phase("listening");
  }

  private beginTurn(): ActiveTurn {
    this.interrupt();
    if (!this.ready || !this.context) throw new Error("Voice connection is not ready.");
    const id = crypto.randomUUID();
    const playback = new PcmPlayback(this.context!, (playbackAt, estimated) => {
      if (this.active?.id !== id) return;
      const latency = Math.max(0, playbackAt - this.active.committedAt);
      this.active.playbackMs = latency;
      this.active.playbackEstimated = estimated;
      try { this.send({ type: "playback.started", turn_id: id, latency_ms: latency }); }
      catch (cause) { this.fail(cause instanceof Error ? cause.message : "Voice connection lost."); return; }
      this.callbacks.phase("speaking");
      this.publishResult(this.active);
    }, () => this.playbackCompleted(id));
    const turn: ActiveTurn = { id, playback, committedAt: performance.now(), serverCompleted: false, sequence: -1, transcript: "" };
    this.active = turn;
    this.callbacks.transcript("");
    this.callbacks.route(null);
    this.callbacks.phase(this.mode === "voice" ? "listening" : "processing");
    return turn;
  }

  private receive(message: StreamEvent): void {
    const turn = this.active;
    if (message.type === "error" && !message.turn_id) {
      this.fail(message.message || "Streaming service failed.");
      return;
    }
    if (!turn || message.turn_id !== turn.id) return;
    if (message.seq !== undefined) {
      if (message.seq <= turn.sequence) return;
      turn.sequence = message.seq;
    }
    switch (message.type) {
      case "error": this.fail(message.message || "This turn failed. Please try again."); break;
      case "turn.cancelled":
        turn.playback.stop();
        this.active = null;
        this.clearResponseTimer();
        this.callbacks.transcript("");
        this.callbacks.route(null);
        if (this.mode === "text") this.stop();
        else this.callbacks.phase("listening");
        break;
      case "transcript.delta":
        turn.transcript += message.delta || "";
        this.callbacks.transcript(turn.transcript);
        break;
      case "transcript.final":
        turn.transcript = message.text || "";
        this.callbacks.transcript(turn.transcript);
        break;
      case "route":
        if (message.decision) this.callbacks.route(message.decision);
        break;
      case "reply":
        if (message.result) {
          turn.firstTextMs ??= performance.now() - turn.committedAt;
          turn.result = normalizeTurnResult({ ...message.result, turn_id: turn.id });
          this.callbacks.route(null);
          this.publishResult(turn);
        }
        break;
      case "audio.delta":
        if (!message.audio) break;
        turn.playback.append(message.audio, message.sample_rate || VOICE_SAMPLE_RATE);
        this.refreshResponseTimer();
        break;
      case "turn.completed":
        this.clearResponseTimer();
        turn.serverCompleted = true;
        if (message.result) {
          turn.result = normalizeTurnResult({ ...message.result, turn_id: turn.id });
          const metrics = message.metrics;
          if (metrics) turn.result.timings = { ...turn.result.timings, ...metrics };
        }
        this.publishResult(turn);
        turn.playback.finish();
        break;
    }
  }

  private publishResult(turn: ActiveTurn): void {
    if (!turn.result) return;
    if (turn.firstTextMs !== undefined) turn.result = { ...turn.result, timings: { ...turn.result.timings, first_text_ms: turn.firstTextMs } };
    if (turn.playbackMs !== undefined) turn.result = { ...turn.result, playback_estimated: turn.playbackEstimated, timings: { ...turn.result.timings, playback_ms: turn.playbackMs } };
    this.callbacks.result(turn.result);
  }

  private playbackCompleted(id: string): void {
    const turn = this.active;
    if (!turn || turn.id !== id || !turn.serverCompleted) return;
    try { this.send({ type: "playback.completed", turn_id: id }); }
    catch (cause) { this.fail(cause instanceof Error ? cause.message : "Voice connection lost."); return; }
    this.active = null;
    this.callbacks.transcript("");
    this.callbacks.route(null);
    if (this.mode === "text" || turn.result?.action === "handoff") this.stop();
    else this.callbacks.phase("listening");
  }

  private send(event: object): void {
    if (this.socket?.readyState !== WebSocket.OPEN) throw new Error("Voice connection is not open.");
    if (this.socket.bufferedAmount > 256_000) throw new Error("Your connection is too slow for live audio. Please reconnect or use text.");
    this.socket.send(JSON.stringify(event));
  }

  private awaitResponse(): void {
    this.callbacks.phase("processing");
    this.refreshResponseTimer();
  }

  private refreshResponseTimer(): void {
    this.clearResponseTimer();
    this.responseTimer = setTimeout(() => this.fail("The response timed out. Reconnect to continue."), 90_000);
  }

  private clearResponseTimer(): void {
    if (this.responseTimer) clearTimeout(this.responseTimer);
    this.responseTimer = null;
  }

  private fail(message: string): void {
    this.stop();
    this.callbacks.error(message);
  }
}
