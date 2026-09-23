import { routerUrl, type LiveRoutingDecision, type TurnResult } from "./voice-api";
import type { VoicePhase } from "./voice-stream";

export interface LiveCaption {
  speaker: "user" | "assistant";
  text: string;
  start_ms: number;
  end_ms: number;
}
interface Callbacks {
  phase: (phase: VoicePhase) => void;
  result: (result: TurnResult) => void;
  route: (decision: LiveRoutingDecision | null) => void;
  caption: (caption: LiveCaption) => void;
  level: (level: number, voiced: boolean) => void;
  error: (message: string) => void;
}

/** Native audio stays on WebRTC; our server owns credentials and delegated work. */
export class VoiceLive {
  private epoch = 0;
  private socket: WebSocket | null = null;
  private peer: RTCPeerConnection | null = null;
  private channel: RTCDataChannel | null = null;
  private context: AudioContext | null = null;
  private microphone: MediaStream | null = null;
  private sources: MediaStreamAudioSourceNode[] = [];
  private input: AnalyserNode | null = null;
  private output: AnalyserNode | null = null;
  private remoteAudio: HTMLAudioElement | null = null;
  private frame: number | null = null;
  private startupTimer: ReturnType<typeof setTimeout> | null = null;
  private rejectStartup: ((error: Error) => void) | null = null;
  private mediaConnected: (() => void) | null = null;
  private ready = false;
  private working = new Set<string>();
  private lastOutputAt = 0;
  private muted = false;
  private silenceSince = 0;
  private phase: VoicePhase = "idle";

  constructor(private readonly sessionId: string, private readonly callbacks: Callbacks) {}

  async start(): Promise<void> {
    const run = ++this.epoch;
    this.changePhase("connecting");
    this.startupTimer = setTimeout(() => { if (run === this.epoch) this.fail("Voice connection timed out. Please try again or type your message."); }, 30_000);
    try {
      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) throw new Error("Microphone access requires HTTPS or localhost.");
      this.context = new AudioContext();
      await this.context.resume();
      if (run !== this.epoch) return;
      const microphone = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true } });
      if (run !== this.epoch) { microphone.getTracks().forEach((track) => track.stop()); return; }
      this.microphone = microphone;
      for (const track of microphone.getAudioTracks()) track.onended = () => this.fail("Microphone disconnected. Start a new conversation.");
      this.input = this.context.createAnalyser();
      this.input.fftSize = 256;
      const input = this.context.createMediaStreamSource(microphone);
      input.connect(this.input);
      this.sources.push(input);
      const peer = new RTCPeerConnection();
      this.peer = peer;
      microphone.getAudioTracks().forEach((track) => peer.addTrack(track, microphone));
      peer.ontrack = (event) => {
        if (run !== this.epoch || !this.context) return;
        const stream = event.streams[0] ?? new MediaStream([event.track]);
        const source = this.context.createMediaStreamSource(stream);
        this.output = this.context.createAnalyser();
        this.output.fftSize = 256;
        source.connect(this.output);
        this.sources.push(source);
        // A media element drives remote WebRTC decoding across browsers.
        // The WebAudio branch only measures levels: never play both paths.
        const audio = new Audio();
        audio.autoplay = true;
        audio.srcObject = stream;
        audio.muted = this.muted;
        this.remoteAudio = audio;
        void audio.play().catch(() => {
          if (run === this.epoch) this.fail("Audio playback was blocked. Allow sound for this site and start the conversation again.");
        });
      };
      peer.onconnectionstatechange = () => {
        if (run !== this.epoch) return;
        if (peer.connectionState === "connected") this.mediaConnected?.();
        if (peer.connectionState === "failed" || peer.connectionState === "closed") this.fail("Voice connection lost. Your request was not resent.");
      };
      this.channel = peer.createDataChannel("oai-events");
      this.channel.onmessage = (event) => {
        if (run !== this.epoch) return;
        try {
          const message = JSON.parse(event.data);
          if (message.type === "session.closed") this.stop();
          else if (message.type === "error") this.fail(message.error?.message ?? message.message ?? "Voice service failed.");
        } catch { this.fail("Invalid voice service event."); }
      };
      const offer = await peer.createOffer();
      if (run !== this.epoch) return;
      await peer.setLocalDescription(offer);
      if (run !== this.epoch) return;
      await this.gatherIce(peer);
      if (run !== this.epoch) return;
      await this.connect(run, peer.localDescription?.sdp ?? offer.sdp ?? "");
      if (run !== this.epoch) return;
      this.ready = true;
      if (this.startupTimer) clearTimeout(this.startupTimer);
      this.startupTimer = null;
      this.changePhase("listening");
      this.meter(run);
    } catch (cause) {
      if (run !== this.epoch) return;
      this.fail(cause instanceof DOMException && cause.name === "NotAllowedError" ? "Allow microphone access, or type your message." : cause instanceof Error ? cause.message : "Unable to start voice conversation.");
    }
  }

  interrupt(): void {
    if (!this.ready) return;
    // Local mute is immediate; resume only once the cancelled audio has drained.
    this.muted = true;
    this.silenceSince = 0;
    if (this.remoteAudio) this.remoteAudio.muted = true;
    this.lastOutputAt = 0;
    this.working.clear();
    this.send({ type: "interrupt" });
    this.changePhase("listening");
  }

  stop(): void {
    ++this.epoch;
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify({ type: "stop" }));
    this.rejectStartup?.(new Error("Conversation ended."));
    this.rejectStartup = null;
    this.mediaConnected = null;
    if (this.startupTimer) clearTimeout(this.startupTimer);
    this.startupTimer = null;
    if (this.frame !== null) cancelAnimationFrame(this.frame);
    this.frame = null;
    this.ready = false;
    this.working.clear();
    if (this.channel) { this.channel.onmessage = null; this.channel.close(); }
    this.channel = null;
    if (this.peer) { this.peer.ontrack = null; this.peer.onconnectionstatechange = null; this.peer.onicegatheringstatechange = null; this.peer.close(); }
    this.peer = null;
    this.microphone?.getTracks().forEach((track) => { track.onended = null; track.stop(); });
    this.microphone = null;
    this.sources.forEach((source) => source.disconnect());
    this.sources = [];
    this.input?.disconnect();
    this.output?.disconnect();
    if (this.remoteAudio) { this.remoteAudio.pause(); this.remoteAudio.srcObject = null; }
    this.input = this.output = null;
    this.remoteAudio = null;
    if (this.context && this.context.state !== "closed") void this.context.close().catch(() => {});
    this.context = null;
    if (this.socket) {
      this.socket.onopen = this.socket.onmessage = this.socket.onclose = this.socket.onerror = null;
      this.socket.close();
    }
    this.socket = null;
    this.callbacks.level(0, false);
    this.callbacks.route(null);
    this.changePhase("idle");
  }

  private gatherIce(peer: RTCPeerConnection): Promise<void> {
    if (peer.iceGatheringState === "complete") return Promise.resolve();
    // Signaling has no trickle ICE channel: send the completed offer once.
    // The shared startup timer and stop() also cancel this wait.
    return new Promise((resolve, reject) => {
      this.rejectStartup = reject;
      peer.onicegatheringstatechange = () => {
        if (peer.iceGatheringState !== "complete") return;
        peer.onicegatheringstatechange = null;
        this.rejectStartup = null;
        resolve();
      };
    });
  }

  private connect(run: number, sdp: string): Promise<void> {
    return new Promise((resolve, reject) => {
      this.rejectStartup = reject;
      const url = new URL(routerUrl("live"), window.location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      const socket = new WebSocket(url.href);
      this.socket = socket;
      socket.onopen = () => { if (run === this.epoch) this.send({ type: "start", session_id: this.sessionId, sdp }); };
      socket.onmessage = (event) => {
        if (run !== this.epoch) return;
        void (async () => {
          try {
            const message = JSON.parse(event.data);
            switch (message.type) {
              case "ready":
                if (typeof message.sdp !== "string") throw new Error("Missing voice connection answer.");
                await this.peer?.setRemoteDescription({ type: "answer", sdp: message.sdp });
                if (run !== this.epoch) return;
                this.mediaConnected = () => {
                  this.rejectStartup = null;
                  this.mediaConnected = null;
                  resolve();
                };
                if (this.peer?.connectionState === "connected") this.mediaConnected();
                break;
              case "caption":
                if ((message.speaker === "user" || message.speaker === "assistant") && typeof message.delta === "string") this.callbacks.caption({ speaker: message.speaker, text: message.delta, start_ms: message.start_ms ?? 0, end_ms: message.end_ms ?? 0 });
                break;
              case "working": this.working.add(message.turn_id); break;
              case "working.done": this.working.delete(message.turn_id); break;
              case "route": this.callbacks.route(message.decision); break;
              case "result": this.working.delete(message.result.turn_id); this.callbacks.result(message.result); this.callbacks.route(null); break;
              case "task.error": this.working.delete(message.turn_id); this.callbacks.error(message.message ?? "Could not complete this request. Please try again."); break;
              case "error": this.fail(message.message ?? "Voice service failed."); break;
              case "closed": this.stop(); break;
            }
          } catch (cause) { if (run === this.epoch) this.fail(cause instanceof Error ? cause.message : "Invalid voice event."); }
        })();
      };
      const disconnected = () => { if (run === this.epoch) this.fail("Voice connection lost. Start again or type your message. Your request was not resent."); };
      socket.onclose = socket.onerror = disconnected;
    });
  }

  private meter(run: number): void {
    const samples = new Float32Array(256);
    const rms = (node: AnalyserNode | null) => {
      if (!node) return 0;
      node.getFloatTimeDomainData(samples);
      return Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
    };
    const tick = () => {
      if (run !== this.epoch) return;
      const input = rms(this.input), output = rms(this.output), now = performance.now();
      if (this.muted) {
        if (output < 0.004) {
          this.silenceSince ||= now;
          if (now - this.silenceSince > 200) { this.muted = false; if (this.remoteAudio) this.remoteAudio.muted = false; }
        } else this.silenceSince = 0;
      }
      if (!this.muted && output > 0.004) this.lastOutputAt = now;
      const speaking = !this.muted && this.lastOutputAt > 0 && now - this.lastOutputAt < 250;
      this.callbacks.level(Math.min(1, Math.max(input, this.muted ? 0 : output) * 8), input > 0.015);
      this.changePhase(speaking ? "speaking" : this.working.size ? "processing" : "listening");
      this.frame = requestAnimationFrame(tick);
    };
    tick();
  }

  private changePhase(phase: VoicePhase): void {
    if (this.phase === phase) return;
    this.phase = phase;
    this.callbacks.phase(phase);
  }
  private send(message: object): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message));
    else this.fail("Voice connection is not open.");
  }
  private fail(message: string): void { this.stop(); this.callbacks.error(message); }
}
