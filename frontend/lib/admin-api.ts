import { routerUrl } from "@/lib/voice-api";

export type Scenario = { id: string; title: string; details: Record<string, unknown> };
export type Settings = { routing_prompt: string; answer_prompt: string; model: string; confidence_threshold: string; workflow_enabled: string; max_uncertain_turns: string; simulation_mode: "simulate" };

export async function adminRequest<T>(path: string, options: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set("X-Admin-Token", token);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(routerUrl(`admin/${path}`), { ...options, headers, credentials: "include", cache: "no-store" });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(typeof error?.detail === "string" ? error.detail : `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function decodeBase64url(value: string): ArrayBuffer {
  const binary = atob(value.replace(/-/g, "+").replace(/_/g, "/").padEnd(Math.ceil(value.length / 4) * 4, "="));
  return Uint8Array.from(binary, (char) => char.charCodeAt(0)).buffer;
}

export function encodeBase64url(value: BufferSource): string {
  const bytes = value instanceof ArrayBuffer ? new Uint8Array(value) : new Uint8Array(value.buffer, value.byteOffset, value.byteLength);
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=/g, "");
}

type Flow = { flow_id: string; options: Record<string, unknown> };

export async function usePasskey(kind: "register" | "login", token?: string): Promise<void> {
  if (!window.PublicKeyCredential || !window.isSecureContext) throw new Error("Passkeys require a secure browser context (HTTPS or localhost).");
  const { flow_id, options } = await adminRequest<Flow>(`auth/${kind}/options`, { method: "POST" }, token);
  if (kind === "register") {
    const publicKey = options as { challenge: string; user: { id: string }; excludeCredentials?: Array<{ id: string }> };
    const credential = await navigator.credentials.create({ publicKey: {
      ...publicKey,
      challenge: decodeBase64url(publicKey.challenge),
      user: { ...publicKey.user, id: decodeBase64url(publicKey.user.id) },
      excludeCredentials: publicKey.excludeCredentials?.map((item) => ({ ...item, id: decodeBase64url(item.id) })),
    } as PublicKeyCredentialCreationOptions }) as PublicKeyCredential | null;
    if (!credential) throw new Error("Passkey setup was cancelled.");
    const response = credential.response as AuthenticatorAttestationResponse;
    await adminRequest("auth/register/finish", { method: "POST", body: JSON.stringify({ flow_id, credential: {
      id: credential.id, rawId: encodeBase64url(credential.rawId), type: credential.type,
      response: { clientDataJSON: encodeBase64url(response.clientDataJSON), attestationObject: encodeBase64url(response.attestationObject) },
      clientExtensionResults: credential.getClientExtensionResults(),
    } }) }, token);
  } else {
    const publicKey = options as { challenge: string; allowCredentials?: Array<{ id: string }> };
    const credential = await navigator.credentials.get({ publicKey: {
      ...publicKey, challenge: decodeBase64url(publicKey.challenge),
      allowCredentials: publicKey.allowCredentials?.map((item) => ({ ...item, id: decodeBase64url(item.id) })),
    } as PublicKeyCredentialRequestOptions }) as PublicKeyCredential | null;
    if (!credential) throw new Error("Sign-in was cancelled.");
    const response = credential.response as AuthenticatorAssertionResponse;
    await adminRequest("auth/login/finish", { method: "POST", body: JSON.stringify({ flow_id, credential: {
      id: credential.id, rawId: encodeBase64url(credential.rawId), type: credential.type,
      response: {
        clientDataJSON: encodeBase64url(response.clientDataJSON), authenticatorData: encodeBase64url(response.authenticatorData),
        signature: encodeBase64url(response.signature), userHandle: response.userHandle ? encodeBase64url(response.userHandle) : null,
      }, clientExtensionResults: credential.getClientExtensionResults(),
    } }) });
  }
}
