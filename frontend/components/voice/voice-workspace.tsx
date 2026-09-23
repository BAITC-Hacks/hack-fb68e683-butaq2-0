"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { VoicePoweredOrb } from "@/components/ui/voice-powered-orb";
import { VoiceSession } from "./voice-session";
import { routerUrl } from "@/lib/voice-api";

type Readiness = "checking" | "ready" | "empty" | "offline";

export function VoiceWorkspace() {
  const [readiness, setReadiness] = useState<Readiness>("checking");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    let mounted = true;
    setReadiness("checking");
    void (async () => {
      try {
        const response = await fetch(routerUrl("scenarios"), { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("Catalogue unavailable");
        const scenarios: unknown = await response.json();
        if (!Array.isArray(scenarios)) throw new Error("Invalid catalogue response");
        if (mounted) setReadiness(scenarios.length ? "ready" : "empty");
      } catch { if (mounted) setReadiness("offline"); }
      finally { clearTimeout(timeout); }
    })();
    return () => { mounted = false; clearTimeout(timeout); controller.abort(); };
  }, [retry]);

  if (readiness === "ready") return <VoiceSession />;
  return (
    <div className="mt-10 w-full max-w-xl">
      <div className="mx-auto h-56 w-56"><VoicePoweredOrb enableVoiceControl={false} /></div>
      <div role="status" className="mb-5 text-sm text-white/70">
        {readiness === "checking" ? "Checking voice service…" : readiness === "empty" ? "Voice is waiting for its scenario catalogue." : "Voice service is unavailable. Check the backend connection and try again."}
      </div>
      {readiness !== "checking" && <Button variant="outline" className="rounded-full" onClick={() => setRetry((value) => value + 1)}>Check again</Button>}
      {readiness === "empty" && <CatalogImport onImported={() => setRetry((value) => value + 1)} />}
    </div>
  );
}

function CatalogImport({ onImported }: { onImported: () => void }) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [token, setToken] = useState("");
  const abort = useRef<AbortController | null>(null);
  useEffect(() => () => abort.current?.abort(), []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (pending) return;
    setError(null);
    const form = event.currentTarget;
    const files = new FormData(form);
    for (const [name, file] of [...files.entries()]) {
      if (!(file instanceof File) || !file.size) { files.delete(name); continue; }
      if (file.size > 2_000_000) { setError(`${file.name} exceeds the 2 MB limit.`); return; }
    }
    if (!files.has("scenarios")) { setError("Select the starter-kit scenarios.json file."); return; }
    const controller = new AbortController();
    abort.current = controller;
    setPending(true);
    try {
      const response = await fetch(routerUrl("admin/catalog/import-files"), { method: "POST", headers: { "X-Admin-Token": token }, body: files, signal: controller.signal });
      const result = await response.json().catch(() => null);
      if (!response.ok) throw new Error(response.status === 403 ? "The admin token is incorrect." : typeof result?.detail === "string" ? result.detail : `Import failed (${response.status}).`);
      if (typeof result?.count !== "number" || result.count < 1) throw new Error("No scenarios were imported.");
      setToken("");
      form.reset();
      onImported();
    } catch (cause) {
      if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : "Import failed. Please retry.");
    } finally { if (!controller.signal.aborted) setPending(false); }
  }

  return (
    <details className="mt-6 rounded-2xl border border-white/15 bg-card p-5 text-left">
      <summary className="cursor-pointer text-sm font-medium">Set up scenarios · administrator</summary>
      <p className="mt-4 text-xs leading-relaxed text-muted-foreground">Import the supplied starter-kit catalogue to enable conversations. The import replaces the current catalogue and its reference data. V2V_API_KEY stays on the backend; use ROUTER_ADMIN_TOKEN here to authorize the import.</p>
      <form onSubmit={submit} className="mt-5 space-y-4">
        <fieldset disabled={pending} className="space-y-4 disabled:opacity-60">
          {[["scenarios", "scenarios.json", true], ["knowledge_base", "knowledge_base.json (optional)", false], ["mock_backend", "mock_backend.json (optional)", false]].map(([name, label, required]) => (
            <label key={String(name)} className="block text-xs text-white/70">{label}<input type="file" name={String(name)} accept=".json,application/json" required={Boolean(required)} className="mt-2 block w-full min-w-0 text-xs file:mr-3 file:rounded-md file:border-0 file:bg-secondary file:px-3 file:py-2 file:text-white" /></label>
          ))}
          <label className="block text-xs text-white/70">Admin token<input type="password" autoComplete="off" required value={token} onChange={(event) => setToken(event.target.value)} className="mt-2 block w-full rounded-lg border border-input bg-background px-3 py-2 text-sm" /></label>
          <Button type="submit" className="w-full">{pending ? "Importing…" : "Import catalogue"}</Button>
        </fieldset>
        {error && <p role="alert" className="text-sm text-red-300">{error}</p>}
      </form>
    </details>
  );
}
