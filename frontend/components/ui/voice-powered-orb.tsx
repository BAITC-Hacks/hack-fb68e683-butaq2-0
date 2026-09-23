"use client";

import { useEffect, useRef, useState, type RefObject } from "react";
import { Renderer, Program, Mesh, Triangle, Vec3 } from "ogl";
import { cn } from "@/lib/utils";

interface VoicePoweredOrbProps {
  className?: string;
  hue?: number;
  enableVoiceControl?: boolean;
  voiceSensitivity?: number;
  maxRotationSpeed?: number;
  maxHoverIntensity?: number;
  onVoiceDetected?: (detected: boolean) => void;
  /** Share the call's level instead of opening another microphone. */
  audioLevelRef?: RefObject<number>;
}

const vert = /* glsl */ `
  precision highp float;
  attribute vec2 position;
  attribute vec2 uv;
  varying vec2 vUv;
  void main() {
    vUv = uv;
    gl_Position = vec4(position, 0.0, 1.0);
  }
`;

// Original supplied orb shader. Time advances only while speech is detected.
const frag = /* glsl */ `
  precision highp float;
  uniform float iTime;
  uniform vec3 iResolution;
  uniform float hue;
  uniform float hover;
  uniform float rot;
  uniform float hoverIntensity;
  varying vec2 vUv;
  vec3 rgb2yiq(vec3 c) {
    return vec3(dot(c, vec3(0.299, 0.587, 0.114)), dot(c, vec3(0.596, -0.274, -0.322)), dot(c, vec3(0.211, -0.523, 0.312)));
  }
  vec3 yiq2rgb(vec3 c) {
    return vec3(c.x + 0.956 * c.y + 0.621 * c.z, c.x - 0.272 * c.y - 0.647 * c.z, c.x - 1.106 * c.y + 1.703 * c.z);
  }
  vec3 adjustHue(vec3 color, float hueDeg) {
    float hueRad = hueDeg * 3.14159265 / 180.0;
    vec3 yiq = rgb2yiq(color);
    float cosA = cos(hueRad);
    float sinA = sin(hueRad);
    float i = yiq.y * cosA - yiq.z * sinA;
    float q = yiq.y * sinA + yiq.z * cosA;
    yiq.y = i;
    yiq.z = q;
    return yiq2rgb(yiq);
  }
  vec3 hash33(vec3 p3) {
    p3 = fract(p3 * vec3(0.1031, 0.11369, 0.13787));
    p3 += dot(p3, p3.yxz + 19.19);
    return -1.0 + 2.0 * fract(vec3(p3.x + p3.y, p3.x + p3.z, p3.y + p3.z) * p3.zyx);
  }
  float snoise3(vec3 p) {
    const float K1 = 0.333333333;
    const float K2 = 0.166666667;
    vec3 i = floor(p + (p.x + p.y + p.z) * K1);
    vec3 d0 = p - (i - (i.x + i.y + i.z) * K2);
    vec3 e = step(vec3(0.0), d0 - d0.yzx);
    vec3 i1 = e * (1.0 - e.zxy);
    vec3 i2 = 1.0 - e.zxy * (1.0 - e);
    vec3 d1 = d0 - (i1 - K2);
    vec3 d2 = d0 - (i2 - K1);
    vec3 d3 = d0 - 0.5;
    vec4 h = max(0.6 - vec4(dot(d0, d0), dot(d1, d1), dot(d2, d2), dot(d3, d3)), 0.0);
    vec4 n = h * h * h * h * vec4(dot(d0, hash33(i)), dot(d1, hash33(i + i1)), dot(d2, hash33(i + i2)), dot(d3, hash33(i + 1.0)));
    return dot(vec4(31.316), n);
  }
  vec4 extractAlpha(vec3 colorIn) {
    float a = max(max(colorIn.r, colorIn.g), colorIn.b);
    return vec4(colorIn.rgb / (a + 1e-5), a);
  }
  const vec3 baseColor1 = vec3(0.611765, 0.262745, 0.996078);
  const vec3 baseColor2 = vec3(0.298039, 0.760784, 0.913725);
  const vec3 baseColor3 = vec3(0.062745, 0.078431, 0.600000);
  const float innerRadius = 0.6;
  const float noiseScale = 0.65;
  float light1(float intensity, float attenuation, float dist) { return intensity / (1.0 + dist * attenuation); }
  float light2(float intensity, float attenuation, float dist) { return intensity / (1.0 + dist * dist * attenuation); }
  vec4 draw(vec2 uv) {
    vec3 color1 = adjustHue(baseColor1, hue);
    vec3 color2 = adjustHue(baseColor2, hue);
    vec3 color3 = adjustHue(baseColor3, hue);
    float ang = atan(uv.y, uv.x);
    float len = length(uv);
    float invLen = len > 0.0 ? 1.0 / len : 0.0;
    float n0 = snoise3(vec3(uv * noiseScale, iTime * 0.5)) * 0.5 + 0.5;
    float r0 = mix(mix(innerRadius, 1.0, 0.4), mix(innerRadius, 1.0, 0.6), n0);
    float d0 = distance(uv, (r0 * invLen) * uv);
    float v0 = light1(1.0, 10.0, d0);
    v0 *= 1.0 - smoothstep(r0, r0 * 1.05, len);
    float cl = cos(ang + iTime * 2.0) * 0.5 + 0.5;
    float a = iTime * -1.0;
    vec2 pos = vec2(cos(a), sin(a)) * r0;
    float d = distance(uv, pos);
    float v1 = light2(1.5, 5.0, d);
    v1 *= light1(1.0, 50.0, d0);
    float v2 = 1.0 - smoothstep(mix(innerRadius, 1.0, n0 * 0.5), 1.0, len);
    float v3 = smoothstep(innerRadius, mix(innerRadius, 1.0, 0.5), len);
    vec3 col = mix(color1, color2, cl);
    col = mix(color3, col, v0);
    col = (col + v1) * v2 * v3;
    return extractAlpha(clamp(col, 0.0, 1.0));
  }
  void main() {
    vec2 fragCoord = vUv * iResolution.xy;
    vec2 center = iResolution.xy * 0.5;
    float size = min(iResolution.x, iResolution.y);
    vec2 uv = (fragCoord - center) / size * 2.0;
    float s = sin(rot);
    float c = cos(rot);
    uv = vec2(c * uv.x - s * uv.y, s * uv.x + c * uv.y);
    uv.x += hover * hoverIntensity * 0.1 * sin(uv.y * 10.0 + iTime);
    uv.y += hover * hoverIntensity * 0.1 * sin(uv.x * 10.0 + iTime);
    vec4 col = draw(uv);
    gl_FragColor = vec4(col.rgb * col.a, col.a);
  }
`;

export function VoicePoweredOrb({ className, hue = 0, enableVoiceControl = true, voiceSensitivity = 1.5, maxRotationSpeed = 1.2, maxHoverIntensity = 0.8, onVoiceDetected, audioLevelRef }: VoicePoweredOrbProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const localLevel = useRef(0);
  const propsRef = useRef({ hue, enableVoiceControl, voiceSensitivity, maxRotationSpeed, maxHoverIntensity, onVoiceDetected, audioLevelRef });
  const [fallback, setFallback] = useState(false);
  useEffect(() => { propsRef.current = { hue, enableVoiceControl, voiceSensitivity, maxRotationSpeed, maxHoverIntensity, onVoiceDetected, audioLevelRef }; });

  // Standalone demo compatibility. The integrated call always supplies audioLevelRef.
  useEffect(() => {
    if (!enableVoiceControl || audioLevelRef) return;
    let disposed = false;
    let stream: MediaStream | undefined;
    let context: AudioContext | undefined;
    let frame = 0;
    void (async () => {
      try {
        stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
        if (disposed) { stream.getTracks().forEach((track) => track.stop()); return; }
        context = new AudioContext();
        await context.resume();
        if (disposed) return;
        const analyser = context.createAnalyser();
        analyser.fftSize = 512;
        context.createMediaStreamSource(stream).connect(analyser);
        const samples = new Float32Array(analyser.fftSize);
        const read = () => {
          analyser.getFloatTimeDomainData(samples);
          const rms = Math.sqrt(samples.reduce((sum, value) => sum + value * value, 0) / samples.length);
          localLevel.current = rms > 0.018 ? Math.min(rms * 8, 1) : 0;
          frame = requestAnimationFrame(read);
        };
        read();
      } catch { localLevel.current = 0; }
    })();
    return () => {
      disposed = true;
      cancelAnimationFrame(frame);
      stream?.getTracks().forEach((track) => track.stop());
      if (context && context.state !== "closed") void context.close().catch(() => {});
      localLevel.current = 0;
    };
  }, [enableVoiceControl, audioLevelRef]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let renderer: Renderer | undefined;
    let geometry: Triangle | undefined;
    let program: Program | undefined;
    let frame = 0;
    let observer: ResizeObserver | undefined;
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    const dispose = () => {
      cancelAnimationFrame(frame);
      observer?.disconnect();
      geometry?.remove();
      program?.remove();
      if (renderer) {
        renderer.gl.canvas.remove();
        renderer.gl.getExtension("WEBGL_lose_context")?.loseContext();
      }
    };
    try {
      renderer = new Renderer({ alpha: true, premultipliedAlpha: true, antialias: true, dpr: Math.min(window.devicePixelRatio || 1, 2) });
      const gl = renderer.gl;
      gl.clearColor(0, 0, 0, 0);
      container.appendChild(gl.canvas);
      geometry = new Triangle(gl);
      program = new Program(gl, { vertex: vert, fragment: frag, uniforms: {
        iTime: { value: 0 }, iResolution: { value: new Vec3(1, 1, 1) }, hue: { value: 0 }, hover: { value: 0 }, rot: { value: 0 }, hoverIntensity: { value: 0 },
      } });
      const mesh = new Mesh(gl, { geometry, program });
      const resize = () => {
        if (!container.clientWidth || !container.clientHeight) return;
        renderer!.setSize(container.clientWidth, container.clientHeight);
        program!.uniforms.iResolution.value.set(gl.canvas.width, gl.canvas.height, gl.canvas.width / gl.canvas.height);
      };
      observer = new ResizeObserver(resize);
      observer.observe(container);
      resize();
      let last = 0;
      let time = 0;
      let rotation = 0;
      let detected = false;
      const draw = (now: number) => {
        const props = propsRef.current;
        const dt = last ? Math.min((now - last) / 1000, 0.05) : 0;
        last = now;
        const level = props.enableVoiceControl ? Math.min((props.audioLevelRef?.current ?? localLevel.current) * props.voiceSensitivity, 1) : 0;
        const active = level > 0.05;
        if (active !== detected) { detected = active; props.onVoiceDetected?.(active); }
        if (active && !reducedMotion.matches) {
          time += dt * (0.5 + level * 2);
          rotation += dt * level * props.maxRotationSpeed * 2;
        }
        const uniforms = program!.uniforms;
        uniforms.iTime.value = time;
        uniforms.rot.value = rotation;
        uniforms.hue.value = props.hue;
        uniforms.hover.value = active && !reducedMotion.matches ? level : 0;
        uniforms.hoverIntensity.value = props.maxHoverIntensity;
        renderer!.render({ scene: mesh });
        frame = requestAnimationFrame(draw);
      };
      frame = requestAnimationFrame(draw);
    } catch {
      dispose();
      setFallback(true);
    }
    return dispose;
  }, []);

  return <div aria-hidden="true" className={cn("relative h-full w-full", className)}><div ref={containerRef} className="absolute inset-0" />{fallback && <div className="absolute inset-[16%] rounded-full border-4 border-violet-300 shadow-[0_0_60px_#8b5cf6,inset_0_0_40px_#38bdf8]" />}</div>;
}
