import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { stripTypeScriptTypes } from "node:module";
import test from "node:test";
async function compiled(name) {
  let source = await readFile(new URL(`../lib/${name}.ts`, import.meta.url), "utf8");
  if (name === "voice-live") source = source.replace('from "./voice-api"', `from "${await compiled("voice-api")}"`);
  return `data:text/javascript;base64,${Buffer.from(stripTypeScriptTypes(source, { mode: "transform" })).toString("base64")}`;
}
const { VoiceLive } = await import(await compiled("voice-live"));
const tick = () => new Promise((resolve) => setImmediate(resolve));
class Socket {
  static OPEN = 1;
  static instances = [];
  constructor(url) { this.url = url; this.readyState = 0; this.sent = []; Socket.instances.push(this); }
  open() { this.readyState = 1; this.onopen?.(); }
  send(value) { this.sent.push(JSON.parse(value)); }
  emit(value) { this.onmessage?.({ data: JSON.stringify(value) }); }
  close() { this.readyState = 3; }
}
class Peer {
  static instances = [];
  static gathering = false;
  constructor() { this.iceGatheringState = Peer.gathering ? "gathering" : "complete"; this.connectionState = "new"; this.tracks = []; Peer.instances.push(this); }
  addTrack(track) { this.tracks.push(track); }
  createDataChannel(name) { assert.equal(name, "oai-events"); this.channel = { close() { this.closed = true; } }; return this.channel; }
  async createOffer() { return { type: "offer", sdp: "browser-offer" }; }
  async setLocalDescription(value) { this.localDescription = value; }
  async setRemoteDescription(value) { this.remoteDescription = value; }
  connected() { this.connectionState = "connected"; this.onconnectionstatechange?.(); }
  close() { this.connectionState = "closed"; }
}
class AudioElement {
  static instances = [];
  static rejectPlayback = false;
  constructor() { this.muted = false; AudioElement.instances.push(this); }
  async play() { this.played = true; if (AudioElement.rejectPlayback) throw new Error("NotAllowedError"); }
  pause() { this.paused = true; }
}
class Context {
  static instances = [];
  constructor() { this.state = "running"; this.destination = {}; Context.instances.push(this); }
  async resume() {}
  async close() { this.state = "closed"; }
  createAnalyser() { return { connect() { assert.fail("meter must not connect to an audible output"); }, disconnect() {}, getFloatTimeDomainData(array) { array.fill(0); } }; }
  createMediaStreamSource() { return { connect() {}, disconnect() {} }; }
  createGain() { return { gain: { value: 1 }, connect() {}, disconnect() {} }; }
}
function setup(getUserMedia) {
  Object.assign(globalThis, { WebSocket: Socket, RTCPeerConnection: Peer, AudioContext: Context, Audio: AudioElement, requestAnimationFrame: (callback) => { globalThis.liveFrame = callback; return 1; }, cancelAnimationFrame() { globalThis.liveFrame = null; }, window: { location: { href: "https://butaq.test/voice/" }, isSecureContext: true } });
  const track = { stopped: false, stop() { this.stopped = true; } };
  const media = { getTracks: () => [track], getAudioTracks: () => [track] };
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: { getUserMedia: getUserMedia ?? (async () => media) } } });
  const events = { phases: [], errors: [], results: [], captions: [] };
  const live = new VoiceLive("session", { phase: (p) => events.phases.push(p), error: (e) => events.errors.push(e), result: (r) => events.results.push(r), caption: (c) => events.captions.push(c), route() {}, level() {} });
  return { live, track, media, events };
}
async function started() {
  const value = setup();
  const pending = value.live.start();
  await tick();
  const socket = Socket.instances.at(-1), peer = Peer.instances.at(-1);
  socket.open();
  socket.emit({ type: "ready", sdp: "server-answer" });
  await tick();
  assert.equal(value.events.phases.at(-1), "connecting", "SDP alone does not prove media connected");
  peer.connected();
  await pending;
  return { ...value, socket, peer };
}
test("Live negotiates audio without sending credentials and keeps actual captions separate from backend drafts", async () => {
  const { live, socket, peer, events, track } = await started();
  assert.equal(socket.url, "wss://butaq.test/router/live");
  assert.deepEqual(socket.sent[0], { type: "start", session_id: "session", sdp: "browser-offer" });
  assert.deepEqual(peer.remoteDescription, { type: "answer", sdp: "server-answer" });
  assert.equal(events.phases.at(-1), "listening");
  socket.emit({ type: "caption", speaker: "assistant", delta: "Spoken", start_ms: 2, end_ms: 4 });
  socket.emit({ type: "working", turn_id: "a" });
  globalThis.liveFrame();
  assert.equal(events.phases.at(-1), "processing");
  socket.emit({ type: "result", result: { turn_id: "a", reply: "Draft" } });
  globalThis.liveFrame();
  assert.equal(events.phases.at(-1), "listening", "completed backend work must not leave the UI processing");
  await tick();
  assert.equal(events.captions[0].text, "Spoken");
  assert.equal(events.results[0].reply, "Draft");
  live.interrupt();
  assert.equal(socket.sent.at(-1).type, "interrupt");
  live.stop();
  assert.equal(track.stopped, true);
  assert.equal(peer.connectionState, "closed");
  assert.equal(peer.channel.closed, true);
  assert.equal(Context.instances.at(-1).state, "closed");
  assert.equal(socket.sent.at(-1).type, "stop");
  assert.equal(socket.onmessage, null);
});
test("ending during microphone permission closes a late microphone and never opens a connection", async () => {
  let grant;
  const value = setup(() => new Promise((resolve) => { grant = resolve; }));
  const socketCount = Socket.instances.length;
  const pending = value.live.start();
  await tick();
  value.live.stop();
  grant(value.media);
  await pending;
  assert.equal(value.track.stopped, true);
  assert.equal(Socket.instances.length, socketCount);
  assert.equal(value.events.phases.at(-1), "idle");
  assert.deepEqual(value.events.errors, []);
});
test("transport failure releases the microphone and does not reconnect or resend", async () => {
  const { live, socket, track, events } = await started();
  const count = Socket.instances.length;
  socket.onclose();
  await tick();
  assert.equal(track.stopped, true);
  assert.equal(events.phases.at(-1), "idle");
  assert.match(events.errors[0], /not resent/);
  assert.equal(Socket.instances.length, count);
  live.stop();
});
test("stopping during signaling settles startup and ignores late answer", async () => {
  const { live, events, track } = setup();
  const pending = live.start();
  await tick();
  const socket = Socket.instances.at(-1);
  socket.open();
  live.stop();
  socket.emit({ type: "ready", sdp: "late" });
  await pending;
  assert.equal(track.stopped, true);
  assert.equal(events.phases.at(-1), "idle");
  assert.deepEqual(events.errors, []);
});

test("Live waits for ICE candidates before signaling and cancellation settles ICE wait", async () => {
  Peer.gathering = true;
  try {
    const { live, track } = setup();
    const count = Socket.instances.length;
    const pending = live.start();
    await tick();
    const peer = Peer.instances.at(-1);
    assert.equal(Socket.instances.length, count, "do not submit an incomplete SDP offer");
    peer.localDescription.sdp = "offer-with-candidates";
    peer.iceGatheringState = "complete";
    peer.onicegatheringstatechange();
    await tick();
    const socket = Socket.instances.at(-1);
    socket.open();
    assert.equal(socket.sent[0].sdp, "offer-with-candidates");
    live.stop();
    await pending;
    assert.equal(track.stopped, true);
    const cancelled = setup();
    const waiting = cancelled.live.start();
    await tick();
    const pendingPeer = Peer.instances.at(-1);
    cancelled.live.stop();
    await waiting;
    assert.equal(pendingPeer.onicegatheringstatechange, null);
    assert.equal(cancelled.track.stopped, true);
  } finally { Peer.gathering = false; }
});

test("remote WebRTC audio is played once via media element, muted on interrupt and released on stop", async () => {
  const { live, peer, media } = await started();
  peer.ontrack({ streams: [media] });
  const audio = AudioElement.instances.at(-1);
  assert.equal(audio.srcObject, media);
  assert.equal(audio.played, true);
  assert.equal(audio.muted, false);
  live.interrupt();
  assert.equal(audio.muted, true);
  globalThis.liveFrame();
  await new Promise((resolve) => setTimeout(resolve, 210));
  globalThis.liveFrame();
  assert.equal(audio.muted, false, "restore output only after cancelled audio drains");
  live.stop();
  assert.equal(audio.paused, true);
  assert.equal(audio.srcObject, null);
});
test("blocked remote playback reports an actionable error and closes microphone and media", async () => {
  const { live, peer, media, track, events } = await started();
  AudioElement.rejectPlayback = true;
  try {
    peer.ontrack({ streams: [media] });
    await tick();
    assert.match(events.errors.at(-1), /Allow sound/);
    assert.equal(track.stopped, true);
    assert.equal(AudioElement.instances.at(-1).srcObject, null);
  } finally { AudioElement.rejectPlayback = false; live.stop(); }
});
