import type { Metadata } from "next";
import { VoiceWorkspace } from "@/components/voice/voice-workspace";
import { Navbar } from "@/components/templates/velorah/navbar";
import { VelorahStyles } from "@/components/templates/velorah/styles";
import { inter, instrumentSerif } from "@/components/templates/velorah/fonts";
import { VideoBackground } from "@/components/ui/video-background";

export const metadata: Metadata = { title: "Voice — Butaq", description: "Talk to Butaq in Russian or Kazakh and follow each routing decision." };

export default function VoicePage() {
  return (
    <div className={`velorah ${inter.variable} ${instrumentSerif.variable} relative isolate min-h-svh bg-background text-foreground antialiased`} style={{ fontFamily: "var(--font-velorah-sans), sans-serif" }}>
      <VelorahStyles />
      <VideoBackground
        src="/media/voice-mindloop.mp4"
        poster="/media/voice-mindloop.jpg"
        className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-cover bg-center after:absolute after:inset-0 after:bg-black/40"
        controlClassName="absolute bottom-6 right-6 z-20 grid size-10 cursor-pointer place-items-center rounded-full border border-white/30 bg-black/50 text-white hover:bg-black/70"
      />
      <Navbar />
      <main className="mx-auto flex max-w-5xl flex-col items-center px-5 pb-20 pt-8 text-center sm:pt-12">
        <p className="mb-4 text-xs uppercase tracking-[0.25em] text-muted-foreground">Butaq · Voice Router</p>
        <h1 className="text-5xl tracking-tight [font-family:var(--font-velorah-serif)] sm:text-7xl">Let&apos;s talk.</h1>
        <VoiceWorkspace />
      </main>
    </div>
  );
}
