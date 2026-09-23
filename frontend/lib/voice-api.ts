export interface LiveRoutingDecision {
  action: "route" | "clarify" | "handoff";
  scenario_id: string | null;
  confidence: number;
  reason: string;
}

export interface TurnResult {
  session_id: string;
  turn_id?: string;
  playback_interrupted?: boolean;
  playback_estimated?: boolean;
  transcript: string;
  reply: string;
  action: "route" | "clarify" | "handoff";
  scenario_id: string | null;
  scenario_title: string | null;
  confidence: number;
  reason: string;
  alternatives: { scenario_id: string; reason: string }[];
  pending_scenario_ids: string[];
  timings: { stt_ms: number; routing_ms: number; response_ms: number; tts_ms: number; total_ms: number; first_audio_ms?: number; first_text_ms?: number; playback_ms?: number };
  audio_base64: string | null;
  audio_content_type: string | null;
  workflow_status: "idle" | "collecting" | "awaiting_confirmation" | "completed" | "cancelled" | "handoff";
  collected_slots: Record<string, unknown>;
  missing_slots: string[];
  confirmation_required: boolean;
  action_trace: { name: string; mode: "read" | "preview" | "simulate" | "handoff" | "skipped"; status: "success" | "error" | "skipped"; result: Record<string, unknown>; error: Record<string, unknown> | null }[];
  completed: boolean;
}

/** Keep the UI stable while frontend/backend containers are replaced independently. */
export function normalizeTurnResult(result: TurnResult): TurnResult {
  return {
    ...result,
    alternatives: Array.isArray(result.alternatives) ? result.alternatives : [],
    pending_scenario_ids: Array.isArray(result.pending_scenario_ids) ? result.pending_scenario_ids : [],
    timings: result.timings ?? { stt_ms: 0, routing_ms: 0, response_ms: 0, tts_ms: 0, total_ms: 0 },
    workflow_status: result.workflow_status ?? "idle",
    collected_slots: result.collected_slots && typeof result.collected_slots === "object" ? result.collected_slots : {},
    missing_slots: Array.isArray(result.missing_slots) ? result.missing_slots : [],
    confirmation_required: result.confirmation_required ?? false,
    action_trace: Array.isArray(result.action_trace) ? result.action_trace : [],
    completed: result.completed ?? false,
  };
}

export function routerUrl(path: string): string {
  const configured = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "");
  // Caddy proxies /router in Docker. Next dev has no proxy because this is a static export.
  const base = configured ?? (process.env.NODE_ENV === "development" ? "http://localhost:8000" : "");
  return `${base}/router/${path}`;
}

export async function requestTurn(input: Blob | string, sessionId: string, signal: AbortSignal): Promise<TurnResult> {
  const voice = typeof input !== "string";
  const body = new FormData();
  if (voice) {
    const extension = input.type.includes("mp4") ? "m4a" : input.type.includes("ogg") ? "ogg" : "webm";
    body.append("audio", input, `utterance.${extension}`);
    body.append("session_id", sessionId);
  }
  const response = await fetch(routerUrl(voice ? "voice" : "text"), {
    method: "POST",
    signal,
    headers: voice ? undefined : { "Content-Type": "application/json" },
    body: voice ? body : JSON.stringify({ session_id: sessionId, text: input, synthesize: true }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    const detail = typeof error?.detail === "string" ? error.detail : `Request failed (${response.status})`;
    throw new Error(detail);
  }
  const result: TurnResult = await response.json();
  if (typeof result.reply !== "string" || typeof result.transcript !== "string" || !result.timings) throw new Error("Invalid voice response from server");
  return normalizeTurnResult(result);
}
