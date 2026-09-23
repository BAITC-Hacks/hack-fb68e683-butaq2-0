import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { stripTypeScriptTypes } from "node:module";

const moduleCache = new Map();
async function compiled(name) {
  if (moduleCache.has(name)) return moduleCache.get(name);
  let source = await readFile(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
  for (const child of ["voice-audio", "voice-api"]) {
    if (source.includes(`from "./${child}"`)) source = source.replaceAll(`from "./${child}"`, `from "${await compiled(child)}"`);
  }
  const outputText = stripTypeScriptTypes(source, { mode: "transform" });
  const url = `data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`;
  moduleCache.set(name, url);
  return url;
}
const { PcmDecoder, PcmPlayback, SpeechGate, pcmToBase64 } = await import(await compiled("voice-audio"));
const { VoiceStream } = await import(await compiled("voice-stream"));
const speech = () => new Int16Array(480).fill(2500);
const silence = () => new Int16Array(480);

class AudioContextFake {
  static instances = [];
  constructor() {
    AudioContextFake.instances.push(this);
    this.currentTime = 1;
    this.baseLatency = 0.01;
    this.outputTime = 0.95;
    this.state = "running";
    this.sources = [];
    this.destination = {};
    this.audioWorklet = { addModule: async (path) => { assert.equal(path, "/voice-capture.worklet.js"); } };
  }
  getOutputTimestamp() { return { contextTime: this.outputTime, performanceTime: performance.now() }; }
  resume() { return Promise.resolve(); }
  close() { this.state = "closed"; return Promise.resolve(); }
  createBuffer(channels, length, rate) { return { duration: length / rate, copyToChannel: (samples) => { this.latestSamples = samples; } }; }
  createBufferSource() {
    const node = { connect() {}, disconnect() {}, stopped: false, start(at) { this.startedAt = at; }, stop() { this.stopped = true; }, end() { this.onended?.(); } };
    this.sources.push(node);
    return node;
  }
  createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
  createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
}

async function outputThrough(context, position) { context.outputTime = position; context.currentTime = Math.max(context.currentTime, position); await new Promise((resolve) => setTimeout(resolve, 15)); }

function bytes(values) { return Buffer.from(values).toString("base64"); }

test("PCM decoder preserves a sample split across chunks and signed values", () => {
  const decoder = new PcmDecoder();
  assert.equal(decoder.decode(bytes([0])).length, 0);
  assert.deepEqual([...decoder.decode(bytes([128, 255, 127]))], [-1, 32767 / 32768]);
  assert.equal(decoder.incomplete, false);
  assert.deepEqual([...decoder.decode(pcmToBase64(new Int16Array([-1234, 4321])))], [-1234 / 32768, 4321 / 32768]);
});

test("playback starts on the first chunk, schedules sequentially and acknowledges only drained completed audio", async (t) => {
  const context = new AudioContextFake();
  let starts = 0;
  let completions = 0;
  const player = new PcmPlayback(context, () => starts++, () => completions++);
  t.after(() => player.stop());
  player.append(pcmToBase64(speech()), 24000);
  player.append(pcmToBase64(speech()), 24000);
  assert.equal(starts, 0);
  await outputThrough(context, 1.011);
  assert.equal(starts, 1);
  assert.equal(context.sources.length, 2);
  assert.equal(context.sources[1].startedAt, context.sources[0].startedAt + 0.02);
  context.sources[0].end();
  player.finish();
  assert.equal(completions, 0);
  context.sources[1].end();
  assert.equal(completions, 0);
  await outputThrough(context, 1.051);
  assert.equal(completions, 1);
  context.sources[1].end();
  assert.equal(completions, 1);
});

test("cancelled playback stops every queued node without a completion acknowledgement", () => {
  const context = new AudioContextFake();
  let completed = false;
  let started = false;
  const player = new PcmPlayback(context, () => { started = true; }, () => { completed = true; });
  player.append(pcmToBase64(speech()), 24000);
  player.append(pcmToBase64(speech()), 24000);
  player.stop();
  context.sources.forEach((source) => { assert.equal(source.stopped, true); source.end(); });
  player.finish();
  assert.equal(completed, false);
  assert.equal(started, false);
});

test("truncated PCM fails instead of acknowledging incomplete speech", () => {
  const player = new PcmPlayback(new AudioContextFake(), () => {}, () => assert.fail("must not acknowledge"));
  player.append(bytes([1]), 24000);
  assert.throws(() => player.finish(), /incomplete sample/);
});

test("microphone gate streams before commit, retains pre-roll, ignores clicks, commits after 500ms silence", () => {
  const events = [];
  const gate = new SpeechGate({ start: () => events.push("start"), audio: () => events.push("audio"), end: (ok) => events.push(ok ? "commit" : "cancel"), level() {} });
  gate.push(speech()); // One isolated click cannot interrupt a response.
  for (let i = 0; i < 20; i++) gate.push(silence());
  assert.deepEqual(events, []);
  for (let i = 0; i < 5; i++) gate.push(speech());
  assert.equal(events[0], "start");
  assert.equal(events.filter((event) => event === "audio").length, 11); // 200ms pre-roll plus next frame.
  for (let i = 0; i < 24; i++) gate.push(silence());
  assert.equal(events.includes("commit"), false);
  gate.push(silence());
  assert.equal(events.at(-1), "commit");
});

test("manual commit rejects utterances with less than 100ms voice; prolonged speech is capped", () => {
  const accepted = [];
  const gate = new SpeechGate({ start() {}, audio() {}, end: (ok) => accepted.push(ok), level() {} });
  for (let i = 0; i < 4; i++) gate.push(speech());
  gate.commit();
  assert.deepEqual(accepted, [false]);
  for (let i = 0; i < 1000; i++) gate.push(speech());
  assert.deepEqual(accepted, [false, true]);
});

test("worklet resamples 48k/44.1k/16k devices into 20ms PCM24k frames without accumulating input", async () => {
  const source = await readFile(new URL("../public/voice-capture.worklet.js", import.meta.url), "utf8");
  for (const rate of [48000, 44100, 16000]) {
    let Processor;
    const frames = [];
    vm.runInNewContext(source, {
      sampleRate: rate,
      AudioWorkletProcessor: class { constructor() { this.port = { postMessage: (buffer) => frames.push(new Int16Array(buffer)) }; } },
      registerProcessor: (name, type) => { assert.equal(name, "voice-capture"); Processor = type; },
    });
    const processor = new Processor();
    for (let consumed = 0; consumed < rate; consumed += 128) processor.process([[new Float32Array(Math.min(128, rate - consumed)).fill(0.5)]]);
    assert.ok(frames.length >= 49 && frames.length <= 50);
    const sampleCount = frames.length * 480 + processor.used;
    assert.ok(sampleCount >= 23998 && sampleCount <= 24000, `24k samples expected at ${rate}Hz, got ${sampleCount}`);
    assert.ok(frames.every((frame) => frame.length === 480 && frame.every((sample) => sample === 16384)));
    assert.ok(processor.buffer.length <= 2);
  }
});

class SocketFake {
  static OPEN = 1;
  static instances = [];
  constructor(url) { this.url = url; this.readyState = 0; this.bufferedAmount = 0; this.sent = []; SocketFake.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(); }
  emit(message) { this.onmessage?.({ data: JSON.stringify(message) }); }
  send(message) { this.sent.push(JSON.parse(message)); }
  close() { this.readyState = 3; }
}
class WorkletFake {
  static instances = [];
  constructor() { this.port = { onmessage: null, close() {} }; WorkletFake.instances.push(this); }
  connect() {}
  disconnect() {}
  frame(samples) { this.port.onmessage?.({ data: samples.buffer }); }
}
const nextTick = () => new Promise((resolve) => setImmediate(resolve));
function result() {
  return { session_id: "session", transcript: "hello", reply: "reply", action: "route", scenario_id: "S01", scenario_title: "Products", confidence: 1, reason: "reason", alternatives: [], pending_scenario_ids: [], timings: { stt_ms: 0, routing_ms: 1, response_ms: 1, tts_ms: 1, total_ms: 3 }, audio_base64: null, audio_content_type: null };
}
async function connected(mode = "voice") {
  globalThis.AudioContext = AudioContextFake;
  globalThis.WebSocket = SocketFake;
  globalThis.AudioWorkletNode = WorkletFake;
  globalThis.window = { location: { href: "https://butaq.test/voice/" }, isSecureContext: true };
  const track = { stopped: false, stop() { this.stopped = true; } };
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: { getUserMedia: async (options) => { assert.equal(options.audio.echoCancellation, true); return { getTracks: () => [track], getAudioTracks: () => [track] }; } } } });
  const events = { phases: [], results: [], transcripts: [], routes: [], errors: [] };
  const stream = new VoiceStream("session", { phase: (value) => events.phases.push(value), result: (value) => events.results.push(value), transcript: (value) => events.transcripts.push(value), route: (value) => events.routes.push(value), level() {}, error: (value) => events.errors.push(value) });
  const starting = stream.start(mode, "hello");
  await nextTick();
  const socket = SocketFake.instances.at(-1);
  socket.open();
  socket.emit({ type: "ready", sample_rate: 24000 });
  await starting;
  return { stream, socket, worklet: WorkletFake.instances.at(-1), context: AudioContextFake.instances.at(-1), track, events };
}

test("voice sends live PCM before commit, plays chunks early, and speech cancels playback before a fresh turn", async () => {
  const { stream, socket, worklet, context, track, events } = await connected();
  try {
    for (let i = 0; i < 5; i++) worklet.frame(speech());
    const first = socket.sent.find((event) => event.type === "audio.append").turn_id;
    assert.ok(!socket.sent.some((event) => event.type === "audio.commit"));
    for (let i = 0; i < 25; i++) worklet.frame(silence());
    assert.equal(socket.sent.at(-1).type, "audio.commit");
    socket.emit({ type: "route", turn_id: first, decision: { action: "route", scenario_id: "S01", confidence: 1, reason: "Products" } });
    assert.equal(events.routes.at(-1).scenario_id, "S01");
    assert.equal(events.results.length, 0);
    socket.emit({ type: "reply", turn_id: first, result: result() });
    assert.equal(events.routes.at(-1), null);
    assert.ok(events.results.at(-1).timings.first_text_ms >= 0);
    socket.emit({ type: "audio.delta", turn_id: first, audio: pcmToBase64(speech()), sample_rate: 24000 });
    await outputThrough(context, context.sources[0].startedAt + 0.001);
    assert.equal(events.phases.at(-1), "speaking");
    assert.equal(context.sources.length, 1);
    for (let i = 0; i < 4; i++) worklet.frame(speech());
    assert.equal(context.sources[0].stopped, true);
    const interruption = socket.sent.findIndex((event) => event.type === "interrupt");
    const second = socket.sent[interruption + 1].turn_id;
    assert.notEqual(second, first);
    assert.equal(socket.sent[interruption + 1].type, "audio.append");
    const count = context.sources.length;
    socket.emit({ type: "audio.delta", turn_id: first, audio: pcmToBase64(speech()), sample_rate: 24000 });
    socket.emit({ type: "turn.completed", turn_id: first, result: result() });
    assert.equal(context.sources.length, count);
    assert.ok(!socket.sent.some((event) => event.type === "playback.completed"));
    assert.equal(events.results.at(-1).playback_interrupted, true);
    worklet.frame(speech());
    for (let i = 0; i < 25; i++) worklet.frame(silence());
    socket.emit({ type: "reply", turn_id: second, result: result() });
    socket.emit({ type: "audio.delta", turn_id: second, audio: pcmToBase64(speech()), sample_rate: 24000 });
    socket.emit({ type: "turn.completed", turn_id: second, result: result(), metrics: { first_audio_ms: 12 } });
    assert.ok(!socket.sent.some((event) => event.type === "playback.completed"));
    context.sources.at(-1).end();
    await outputThrough(context, context.sources.at(-1).startedAt + 0.021);
    assert.deepEqual(socket.sent.at(-1), { type: "playback.completed", turn_id: second });
    assert.equal(events.phases.at(-1), "listening");
  } finally { stream.stop(); }
  assert.equal(track.stopped, true);
  assert.equal(context.state, "closed");
});

test("text uses streaming output and ignores duplicate/late events after stop", async () => {
  const { stream, socket, context, events } = await connected("text");
  const text = socket.sent.find((event) => event.type === "text");
  assert.equal(text.text, "hello");
  const turn = text.turn_id;
  socket.emit({ type: "transcript.delta", turn_id: turn, delta: "hel", seq: 0 });
  socket.emit({ type: "transcript.delta", turn_id: turn, delta: "lo", seq: 1 });
  assert.equal(events.transcripts.at(-1), "hello");
  socket.emit({ type: "audio.delta", turn_id: turn, audio: pcmToBase64(speech()), sample_rate: 24000, seq: 2 });
  socket.emit({ type: "audio.delta", turn_id: turn, audio: pcmToBase64(speech()), sample_rate: 24000, seq: 2 });
  assert.equal(context.sources.length, 1);
  const lateMessage = socket.onmessage;
  stream.stop();
  const phases = events.phases.length;
  lateMessage({ data: JSON.stringify({ type: "reply", turn_id: turn, result: result(), seq: 3 }) });
  assert.equal(events.results.length, 0);
  assert.equal(events.phases.length, phases);
  assert.equal(context.sources[0].stopped, true);
});

test("connection failure cleans up and never retries a turn automatically", async () => {
  const { stream, socket, context, events } = await connected("text");
  socket.onclose();
  assert.equal(events.phases.at(-1), "idle");
  assert.match(events.errors.at(-1), /not resent/);
  assert.equal(socket.sent.filter((event) => event.type === "text").length, 1);
  assert.equal(context.state, "closed");
  assert.equal(await stream.textAccepted(), false);
  stream.stop();
});

test("text draft may clear only after its own server acceptance, never on socket readiness", async () => {
  const { stream, socket } = await connected("text");
  const turn = socket.sent.find((event) => event.type === "text").turn_id;
  let settled = false;
  const accepted = stream.textAccepted().then((value) => { settled = true; return value; });
  try {
    await Promise.resolve();
    assert.equal(settled, false);
    socket.emit({ type: "turn.started", turn_id: "stale" });
    await Promise.resolve();
    assert.equal(settled, false);
    socket.emit({ type: "turn.started", turn_id: turn });
    assert.equal(await accepted, true);
  } finally { stream.stop(); }
});
