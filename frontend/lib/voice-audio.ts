/** Browser audio primitives, independent of React and the wire protocol. */
export const VOICE_SAMPLE_RATE = 24_000;

export function pcmToBase64(samples: Int16Array): string {
  const bytes = new Uint8Array(samples.length * 2);
  const view = new DataView(bytes.buffer);
  samples.forEach((sample, index) => view.setInt16(index * 2, sample, true));
  let binary = "";
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return btoa(binary);
}

/** Network chunks may split a signed 16-bit sample between two messages. */
export class PcmDecoder {
  private pending: number | null = null;

  decode(encoded: string): Float32Array {
    const binary = atob(encoded);
    const bytes = new Uint8Array(binary.length + (this.pending === null ? 0 : 1));
    let offset = 0;
    if (this.pending !== null) bytes[offset++] = this.pending;
    for (let index = 0; index < binary.length; index++) bytes[offset + index] = binary.charCodeAt(index);
    this.pending = bytes.length % 2 ? bytes[bytes.length - 1] : null;
    const view = new DataView(bytes.buffer);
    return Float32Array.from({ length: Math.floor(bytes.length / 2) }, (_, index) => view.getInt16(index * 2, true) / 32768);
  }

  get incomplete(): boolean { return this.pending !== null; }
}

export class PcmPlayback {
  private decoder = new PcmDecoder();
  private sources = new Set<AudioBufferSourceNode>();
  private nextAt = 0;
  private finished = false;
  private started = false;
  private notified = false;
  private firstAt: number | null = null;
  private outputTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private readonly context: AudioContext,
    private readonly onStarted: (playbackAtMs: number, estimated: boolean) => void,
    private readonly onDrained: () => void,
  ) {}

  append(audio: string, sampleRate: number): void {
    if (this.finished) return;
    if (sampleRate !== VOICE_SAMPLE_RATE) throw new Error("Unsupported voice audio format.");
    const samples = this.decoder.decode(audio);
    if (!samples.length) return;
    const buffer = this.context.createBuffer(1, samples.length, sampleRate);
    buffer.copyToChannel(samples as Float32Array<ArrayBuffer>, 0);
    const source = this.context.createBufferSource();
    source.buffer = buffer;
    source.connect(this.context.destination);
    this.sources.add(source);
    const when = Math.max(this.context.currentTime + 0.01, this.nextAt);
    this.nextAt = when + buffer.duration;
    source.onended = () => {
      this.sources.delete(source);
      source.disconnect();
      this.checkDrained();
    };
    source.start(when);
    this.firstAt ??= when;
    this.checkOutput();
  }

  finish(): void {
    if (this.decoder.incomplete) throw new Error("The audio stream ended with an incomplete sample.");
    this.finished = true;
    this.checkDrained();
  }

  stop(): void {
    this.finished = true;
    this.notified = true; // Cancellation never acknowledges successful playback.
    if (this.outputTimer) clearTimeout(this.outputTimer);
    this.outputTimer = null;
    for (const source of this.sources) {
      source.onended = null;
      source.stop();
      source.disconnect();
    }
    this.sources.clear();
  }

  private checkDrained(): void { this.checkOutput(); }

  private checkOutput(): void {
    if (this.notified || this.outputTimer) return;
    const timestamp = this.context.getOutputTimestamp?.();
    const measured = Boolean(timestamp?.contextTime && timestamp?.performanceTime);
    const outputTime = measured ? timestamp!.contextTime! : this.context.currentTime - (this.context.baseLatency || 0) - (this.context.outputLatency || 0);
    const performanceTime = measured ? timestamp!.performanceTime! : performance.now();
    if (!this.started && this.firstAt !== null && outputTime >= this.firstAt) {
      this.started = true;
      this.onStarted(performanceTime + (this.firstAt - outputTime) * 1000, !measured);
      if (this.notified) return;
    }
    if (this.finished && !this.sources.size && (this.firstAt === null || outputTime >= this.nextAt)) {
      this.notified = true;
      this.onDrained();
      return;
    }
    if ((!this.started && this.firstAt !== null) || (this.finished && !this.sources.size)) {
      this.outputTimer = setTimeout(() => { this.outputTimer = null; this.checkOutput(); }, 10);
    }
  }
}

interface SpeechCallbacks {
  start: () => void;
  audio: (samples: Int16Array) => void;
  end: (accepted: boolean) => void;
  level: (rms: number, voiced: boolean) => void;
}

/** Small client VAD: retain consonant onset, commit pauses, ignore isolated clicks. */
export class SpeechGate {
  private preRoll: Int16Array[] = [];
  private preRollMs = 0;
  private onsetMs = 0;
  private voiceMs = 0;
  private silenceMs = 0;
  private durationMs = 0;
  private capturing = false;

  constructor(private readonly callbacks: SpeechCallbacks) {}

  push(samples: Int16Array): void {
    const duration = samples.length / VOICE_SAMPLE_RATE * 1000;
    const rms = Math.sqrt(samples.reduce((sum, sample) => sum + (sample / 32768) ** 2, 0) / Math.max(samples.length, 1));
    const voiced = rms > 0.02;
    this.callbacks.level(rms, voiced);
    if (!this.capturing) {
      this.preRoll.push(samples);
      this.preRollMs += duration;
      while (this.preRollMs > 200 && this.preRoll.length > 1) {
        this.preRollMs -= this.preRoll.shift()!.length / VOICE_SAMPLE_RATE * 1000;
      }
      this.onsetMs = voiced ? this.onsetMs + duration : 0;
      if (this.onsetMs < 80) return;
      this.capturing = true;
      this.voiceMs = this.onsetMs;
      this.durationMs = this.preRollMs;
      this.silenceMs = 0;
      this.callbacks.start();
      for (const frame of this.preRoll) this.callbacks.audio(frame);
      this.preRoll = [];
      this.preRollMs = 0;
      return;
    }
    this.callbacks.audio(samples);
    this.durationMs += duration;
    if (voiced) { this.voiceMs += duration; this.silenceMs = 0; }
    else this.silenceMs += duration;
    if (this.silenceMs >= 500 || this.durationMs >= 20_000) this.commit();
  }

  commit(): void {
    if (!this.capturing) return;
    const accepted = this.voiceMs >= 100;
    this.reset();
    this.callbacks.end(accepted);
  }

  reset(): void {
    this.preRoll = [];
    this.preRollMs = this.onsetMs = this.voiceMs = this.silenceMs = this.durationMs = 0;
    this.capturing = false;
  }
}
