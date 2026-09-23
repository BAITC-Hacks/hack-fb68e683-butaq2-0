import type { Metadata } from "next";
import { VoiceWorkspace } from "@/components/voice/voice-workspace";
import { Navbar } from "@/components/templates/velorah/navbar";
import { VelorahStyles } from "@/components/templates/velorah/styles";
import { inter, instrumentSerif } from "@/components/templates/velorah/fonts";

export const metadata: Metadata = { title: "Voice — Butaq", description: "Talk to Butaq in Russian or Kazakh and follow each routing decision." };

export default function VoicePage() {
  return (
    <div className={`velorah ${inter.variable} ${instrumentSerif.variable} min-h-svh bg-background text-foreground antialiased`} style={{ fontFamily: "var(--font-velorah-sans), sans-serif" }}>
      <VelorahStyles />
      <Navbar />
      <main className="mx-auto flex max-w-5xl flex-col items-center px-5 pb-20 pt-8 text-center sm:pt-12">
        <p className="mb-4 text-xs uppercase tracking-[0.25em] text-muted-foreground">Butaq · Voice Router</p>
        <h1 className="text-5xl tracking-tight [font-family:var(--font-velorah-serif)] sm:text-7xl">Let&apos;s talk.</h1>
        <p className="mt-4 max-w-lg text-sm leading-relaxed text-muted-foreground">Speak naturally in Russian or Kazakh. Follow the conversation and see why each scenario is selected.</p>
        <VoiceWorkspace />
      </main>
    </div>
  );
}
