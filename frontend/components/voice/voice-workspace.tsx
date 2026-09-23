"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { VoicePoweredOrb } from "@/components/ui/voice-powered-orb";
import { VoiceSession } from "./voice-session";
import { routerUrl, type CatalogStatus } from "@/lib/voice-api";

type Readiness = "checking" | "ready" | "empty" | "offline";

export function VoiceWorkspace() {
  const [readiness, setReadiness] = useState<Readiness>("checking");
  const [retry, setRetry] = useState(0);
  const [catalog, setCatalog] = useState<CatalogStatus | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 10_000);
    let mounted = true;
    setReadiness("checking");
    void (async () => {
      try {
        const response = await fetch(routerUrl("catalog/status"), { signal: controller.signal, cache: "no-store" });
        if (!response.ok) throw new Error("Catalogue unavailable");
        const status: CatalogStatus = await response.json();
        if (!Number.isInteger(status.scenario_count)) throw new Error("Invalid catalogue response");
        if (mounted) { setCatalog(status); setReadiness(status.scenario_count ? "ready" : "empty"); }
      } catch { if (mounted) setReadiness("offline"); }
      finally { clearTimeout(timeout); }
    })();
    return () => { mounted = false; clearTimeout(timeout); controller.abort(); };
  }, [retry]);

  if (readiness === "ready" && catalog) return <div className="w-full max-w-4xl">
    <p className="mt-5 text-xs text-white/70">{catalog.name} · {catalog.scenario_count} сценариев · {catalog.as_of_date ?? "демоданные"}</p>
    {(!catalog.official_ids_complete || !catalog.knowledge_loaded || !catalog.records_loaded) && <p role="status" className="mx-auto mt-3 max-w-lg rounded-xl border border-amber-200/30 bg-black/80 p-3 text-xs text-amber-100">Набор для жюри не полностью подключён. Загрузите все пять файлов официального набора в <a href="/admin/import/" className="underline">админ-панели</a>. / Ресми деректер толық қосылмаған.</p>}
    <VoiceSession />
  </div>;
  return (
    <div className="mt-10 w-full max-w-xl">
      <div className="mx-auto h-56 w-56"><VoicePoweredOrb enableVoiceControl={false} /></div>
      <div role="status" className="mb-5 text-sm text-white/70">
        {readiness === "checking" ? "Проверяем каталог… / Каталог тексерілуде…" : readiness === "empty" ? "Нет сценариев — обратитесь к администратору. / Сценарийлер жоқ." : "Backend недоступен. Проверьте запуск сервера. / Сервер қолжетімсіз."}
      </div>
      {readiness !== "checking" && <Button variant="outline" className="rounded-full" onClick={() => setRetry((value) => value + 1)}>Повторить / Қайталау</Button>}
    </div>
  );
}
