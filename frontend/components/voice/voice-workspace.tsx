"use client";

import { useEffect, useState } from "react";
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

  if (readiness === "ready") return <div className="w-full max-w-4xl"><VoiceSession /></div>;
  return (
    <div className="mt-10 w-full max-w-xl">
      <div className="mx-auto h-56 w-56"><VoicePoweredOrb enableVoiceControl={false} /></div>
      <div role="status" className="mb-5 text-sm text-white/70">
        {readiness === "checking" ? "Preparing your conversation…" : readiness === "empty" ? "Scenarios are not ready yet. Please try again shortly." : "Voice service is unavailable. Please try again shortly."}
      </div>
      {readiness !== "checking" && <Button variant="outline" className="rounded-full" onClick={() => setRetry((value) => value + 1)}>Check again</Button>}
    </div>
  );
}
