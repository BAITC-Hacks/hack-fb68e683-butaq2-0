/* Resample the device rate to mono PCM16/24kHz in 20 ms transferable frames. */
class VoiceCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.buffer = [];
    this.position = 0;
    this.frame = new Int16Array(480);
    this.used = 0;
  }

  process(inputs) {
    const input = inputs[0]?.[0];
    if (!input?.length) return true;
    for (const sample of input) this.buffer.push(sample);
    const step = sampleRate / 24000;
    while (this.position + 1 < this.buffer.length) {
      const index = Math.floor(this.position);
      const fraction = this.position - index;
      const value = Math.max(-1, Math.min(1, this.buffer[index] * (1 - fraction) + this.buffer[index + 1] * fraction));
      this.frame[this.used++] = Math.round(value * (value < 0 ? 32768 : 32767));
      if (this.used === this.frame.length) {
        this.port.postMessage(this.frame.buffer, [this.frame.buffer]);
        this.frame = new Int16Array(480);
        this.used = 0;
      }
      this.position += step;
    }
    const consumed = Math.min(Math.floor(this.position), this.buffer.length - 1);
    this.buffer.splice(0, consumed);
    this.position -= consumed;
    return true;
  }
}

registerProcessor("voice-capture", VoiceCapture);
