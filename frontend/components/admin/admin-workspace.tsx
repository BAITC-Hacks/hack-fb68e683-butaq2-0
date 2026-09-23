"use client";

import { useCallback, useEffect, useState } from "react";
import { ArrowRight, Check, Fingerprint, LogOut, Plus, RefreshCw, ShieldCheck, Trash2, UploadCloud } from "lucide-react";
import { adminRequest, usePasskey, type Scenario, type Settings } from "@/lib/admin-api";
import { routerUrl } from "@/lib/voice-api";
import { LoginBackground } from "./login-background";

type Section = "overview" | "settings" | "scenarios" | "import";
const links: { key: Section; label: string; href: string; number: string }[] = [
  { key: "overview", label: "Overview", href: "/admin/", number: "01" },
  { key: "settings", label: "Configuration", href: "/admin/settings/", number: "02" },
  { key: "scenarios", label: "Scenarios", href: "/admin/scenarios/", number: "03" },
  { key: "import", label: "Import data", href: "/admin/import/", number: "04" },
];

export function AdminWorkspace({ section }: { section: Section }) {
  const [auth, setAuth] = useState<"checking" | "locked" | "passkey" | "token">("checking");
  const [token, setToken] = useState("");
  const [draftToken, setDraftToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [settings, setSettings] = useState<Settings | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [selected, setSelected] = useState<Scenario | null>(null);
  const [details, setDetails] = useState("");

  const load = useCallback(async (accessToken?: string) => {
    const [config, catalog] = await Promise.all([
      adminRequest<Settings>("settings", {}, accessToken),
      fetch(routerUrl("scenarios"), { cache: "no-store" }).then(async (response) => {
        if (!response.ok) throw new Error("Unable to load scenarios");
        return response.json() as Promise<Scenario[]>;
      }),
    ]);
    setSettings(config);
    setScenarios(catalog);
  }, []);

  useEffect(() => {
    let active = true;
    adminRequest("auth/me").then(() => {
      if (!active) return;
      setAuth("passkey");
      void load().catch((err: Error) => setError(err.message));
    }).catch(() => { if (active) setAuth("locked"); });
    return () => { active = false; };
  }, [load]);

  async function run(action: () => Promise<unknown>, success: string) {
    setBusy(true); setError(""); setNotice("");
    try { await action(); setNotice(success); }
    catch (err) { setError(err instanceof Error ? err.message : "Something went wrong"); }
    finally { setBusy(false); }
  }

  function choose(scenario: Scenario) { setSelected(scenario); setDetails(JSON.stringify(scenario.details, null, 2)); }

  if (auth === "checking") return <main className="admin-loading" role="status">Checking admin access…</main>;

  if (auth === "locked") return (
    <main className="admin-signin">
      <LoginBackground />
      <div className="text-center">
        <p className="mb-4 text-xs uppercase tracking-[0.25em] text-muted-foreground">Butaq · Admin</p>
        <h1 className="text-5xl tracking-tight [font-family:var(--font-velorah-serif)] sm:text-7xl">Login Admin</h1>
      </div>
      <div className="admin-auth-card" aria-label="Admin sign in">
          <label className="admin-label" htmlFor="admin-token">Router admin token</label>
          <input id="admin-token" className="admin-input" type="password" autoComplete="off" placeholder="Enter your token" value={draftToken} onChange={(event) => setDraftToken(event.target.value)} />
          <button className="admin-secondary" disabled={busy || !draftToken} onClick={() => void run(async () => { await adminRequest("auth/token/login", { method: "POST" }, draftToken); await load(); setToken(draftToken); setDraftToken(""); setAuth("token"); }, "Token verified.")}>Continue with token <ArrowRight size={16} /></button>
          <p className="admin-hint">First time? Sign in with the router token, then register a passkey from Overview.</p>
          <button className="admin-secondary" disabled={busy} onClick={() => void run(async () => { await usePasskey("login"); setAuth("passkey"); await load(); }, "Signed in successfully.")}><Fingerprint size={19} /> Sign in with Face ID <ArrowRight size={17} /></button>
          {error && <p className="admin-error" role="alert">{error}</p>}
      </div>
    </main>
  );

  return (
    <div className="admin-shell">
      <aside className="admin-sidebar">
        <div className="admin-side-caption">CONTROL ROOM <span> / 2026</span></div>
        <nav aria-label="Admin navigation">{links.map((link) => <a key={link.key} href={link.href} aria-current={section === link.key ? "page" : undefined} className={`admin-nav-item ${section === link.key ? "active" : ""}`}><span>{link.number}</span>{link.label}<ArrowRight size={15} /></a>)}</nav>
        <div className="admin-side-bottom"><div className="admin-online"><span className="admin-dot" /> ROUTER ONLINE</div><p>Changes to your configuration take effect on the next conversation turn.</p><button onClick={() => void run(async () => { await adminRequest("auth/logout", { method: "POST" }); setToken(""); setAuth("locked"); }, "Signed out.")}><LogOut size={16} /> Sign out</button></div>
      </aside>
      <div className="admin-content">
        <header className="admin-topbar"><span>BUTAQ / ADMIN / {section.toUpperCase()}</span><span><ShieldCheck size={16} /> ADMIN SESSION</span></header>
        <main className="admin-main">
          {error && <div className="admin-message admin-error" role="alert">{error}<button onClick={() => setError("")}>Dismiss</button></div>}
          {notice && <div className="admin-message admin-success" role="status"><Check size={16} />{notice}<button onClick={() => setNotice("")}>Dismiss</button></div>}
          {section === "overview" && <>
            <div className="admin-hero"><p className="admin-kicker">THE CONTROL ROOM / 01</p><h1>Good conversations<br />start <em>here.</em></h1><p>Shape how Butaq listens, decides, and responds. Your edits go live on the next turn.</p></div>
            <div className="admin-stats"><div><span>ACTIVE SCENARIOS</span><strong>{scenarios.length.toString().padStart(2, "0")}</strong><small>In the live catalogue</small></div><div><span>ROUTING MODEL</span><strong className="admin-model">{settings?.model ?? "—"}</strong><small>Current inference model</small></div><div><span>CONFIDENCE FLOOR</span><strong>{settings ? `${Math.round(Number(settings.confidence_threshold) * 100)}%` : "—"}</strong><small>Below this, ask to clarify</small></div></div>
            <div className="admin-section-heading"><h2>Workspace</h2><span>YOUR TOOLS / 03</span></div>
            <div className="admin-tool-grid">{links.slice(1).map((link) => <a href={link.href} className="admin-tool" key={link.key}><span>{link.number} / CONFIGURE</span><h3>{link.label}</h3><p>{link.key === "settings" ? "Tune instructions, model, and routing confidence." : link.key === "scenarios" ? "Review and edit the paths behind each conversation." : "Bring a new catalogue and grounding data online."}</p><ArrowRight size={21} /></a>)}</div>
            <div className="admin-enroll"><div><p className="admin-kicker">DEVICE SECURITY</p><h2>Add a device passkey</h2><p>Enroll Face ID, Touch ID, or another platform passkey. Your router token is required to authorize enrollment.</p></div><button className="admin-secondary" disabled={busy} onClick={() => { const supplied = token || window.prompt("Enter the router admin token to enroll this device") || ""; if (supplied) void run(() => usePasskey("register", supplied), "Device passkey registered. You can use it next time you sign in."); }}><Plus size={17} /> Register passkey</button></div>
          </>}
          {section === "settings" && <><PageHeading number="02" eyebrow="LIVE CONFIGURATION" title={<>The voice behind<br /><em>every answer.</em></>} description="Adjust the instructions and decision thresholds that guide the router. Saved changes apply on the next turn." />{settings && <form className="admin-form" onSubmit={(event) => { event.preventDefault(); void run(async () => setSettings(await adminRequest<Settings>("settings", { method: "PATCH", body: JSON.stringify(settings) }, token)), "Configuration saved. Changes are live for the next turn."); }}><div className="admin-form-row"><label htmlFor="model">Routing model<span>Model ID used to select a scenario</span></label><input id="model" className="admin-input" value={settings.model} onChange={(event) => setSettings({ ...settings, model: event.target.value })} required /></div><div className="admin-form-row"><label htmlFor="threshold">Confidence threshold<span>When confidence falls below this value, Butaq asks for clarification</span></label><div className="admin-threshold"><input id="threshold" type="range" min="0" max="1" step="0.01" value={settings.confidence_threshold} onChange={(event) => setSettings({ ...settings, confidence_threshold: event.target.value })} /><input aria-label="Confidence threshold value" className="admin-input" type="number" min="0" max="1" step="0.01" value={settings.confidence_threshold} onChange={(event) => setSettings({ ...settings, confidence_threshold: event.target.value })} required /></div></div><div className="admin-form-row"><label htmlFor="routing-prompt">Routing instructions<span>How the assistant selects, clarifies, or escalates a scenario</span></label><textarea id="routing-prompt" className="admin-input" rows={12} value={settings.routing_prompt} onChange={(event) => setSettings({ ...settings, routing_prompt: event.target.value })} required /></div><div className="admin-form-row"><label htmlFor="answer-prompt">Response instructions<span>How the assistant speaks once a scenario is selected</span></label><textarea id="answer-prompt" className="admin-input" rows={12} value={settings.answer_prompt} onChange={(event) => setSettings({ ...settings, answer_prompt: event.target.value })} required /></div><div className="admin-form-actions"><button type="button" className="admin-secondary" disabled={busy} onClick={() => void run(async () => setSettings(await adminRequest<Settings>("settings", {}, token)), "Latest configuration loaded.")}><RefreshCw size={16} /> Reload</button><button className="admin-primary" disabled={busy} type="submit">Save changes <ArrowRight size={17} /></button></div></form>}</>}
          {section === "scenarios" && <><PageHeading number="03" eyebrow="LIVE CATALOGUE" title={<>Every path,<br /><em>in one place.</em></>} description="Select a scenario to edit its title and JSON details. The router reads the latest catalogue on every turn." /><div className="admin-scenario-layout"><div className="admin-scenario-list"><div className="admin-list-top"><span>{scenarios.length} SCENARIOS</span><button title="Add scenario" aria-label="Add scenario" onClick={() => choose({ id: "", title: "", details: {} })}><Plus size={18} /></button></div>{scenarios.map((scenario, index) => <button className={`admin-scenario-item ${selected?.id === scenario.id ? "active" : ""}`} key={scenario.id} onClick={() => choose(scenario)}><span>{String(index + 1).padStart(2, "0")}</span><span><strong>{scenario.title}</strong><small>{scenario.id}</small></span><ArrowRight size={16} /></button>)}</div><div className="admin-editor">{selected ? <form onSubmit={(event) => { event.preventDefault(); void run(async () => { const payload = { ...selected, details: JSON.parse(details) as Record<string, unknown> }; const saved = await adminRequest<Scenario>(`scenarios/${encodeURIComponent(payload.id)}`, { method: "PUT", body: JSON.stringify(payload) }, token); await load(token); choose(saved); }, "Scenario saved and available to the router."); }}><p className="admin-kicker">SCENARIO EDITOR</p><label className="admin-label" htmlFor="scenario-id">Scenario ID</label><input id="scenario-id" className="admin-input" value={selected.id} onChange={(event) => setSelected({ ...selected, id: event.target.value })} readOnly={scenarios.some((item) => item.id === selected.id)} required /><label className="admin-label" htmlFor="scenario-title">Title</label><input id="scenario-title" className="admin-input" value={selected.title} onChange={(event) => setSelected({ ...selected, title: event.target.value })} required /><label className="admin-label" htmlFor="scenario-details">Details · JSON</label><textarea id="scenario-details" className="admin-input admin-code" rows={15} value={details} onChange={(event) => setDetails(event.target.value)} spellCheck={false} required /><div className="admin-editor-actions"><button type="submit" className="admin-primary" disabled={busy}>Save scenario <ArrowRight size={16} /></button>{scenarios.some((item) => item.id === selected.id) && <button type="button" className="admin-danger" disabled={busy} onClick={() => { if (window.confirm(`Delete ${selected.title}?`)) void run(async () => { await adminRequest(`scenarios/${encodeURIComponent(selected.id)}`, { method: "DELETE" }, token); setSelected(null); await load(token); }, "Scenario deleted."); }}><Trash2 size={16} /> Delete</button>}</div></form> : <div className="admin-empty"><Plus size={28} /><h3>Choose a scenario</h3><p>Select an item from the catalogue or create a new one to get started.</p></div>}</div></div></>}
          {section === "import" && <><PageHeading number="04" eyebrow="CATALOGUE IMPORT" title={<>A fresh source<br /><em>of knowledge.</em></>} description="Replace the live scenario catalogue in one atomic update. Your routing prompts and model settings are preserved." /><form className="admin-import" onSubmit={(event) => { event.preventDefault(); const element = event.currentTarget; const form = new FormData(element); void run(async () => { await adminRequest<{ count: number }>("catalog/import-files", { method: "POST", body: form }, token); await load(token); element.reset(); }, "Catalogue imported successfully."); }}><div className="admin-import-icon"><UploadCloud size={34} strokeWidth={1.3} /></div><h2>Import catalogue files</h2><p>JSON files only · Maximum 2 MB per file. Scenarios are required; grounding data is optional.</p><label className="admin-upload" htmlFor="scenarios-file">Scenarios JSON <span>Required</span><input id="scenarios-file" name="scenarios" type="file" accept=".json,application/json" required /></label><label className="admin-upload" htmlFor="knowledge-file">Knowledge base JSON <span>Optional</span><input id="knowledge-file" name="knowledge_base" type="file" accept=".json,application/json" /></label><label className="admin-upload" htmlFor="backend-file">Mock backend JSON <span>Optional</span><input id="backend-file" name="mock_backend" type="file" accept=".json,application/json" /></label><button className="admin-primary" type="submit" disabled={busy}>Replace live catalogue <ArrowRight size={17} /></button></form><p className="admin-import-note">This replaces all existing scenarios. Review your files before importing.</p></>}
        </main>
      </div>
    </div>
  );
}

function PageHeading({ number, eyebrow, title, description }: { number: string; eyebrow: string; title: React.ReactNode; description: string }) {
  return <div className="admin-page-heading"><p className="admin-kicker">{number} / {eyebrow}</p><h1>{title}</h1><p>{description}</p></div>;
}
