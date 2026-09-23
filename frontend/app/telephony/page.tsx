import type { Metadata } from "next";
import { Phone } from "lucide-react";
import { Navbar } from "@/components/templates/velorah/navbar";
import { VelorahStyles } from "@/components/templates/velorah/styles";
import { inter, instrumentSerif } from "@/components/templates/velorah/fonts";
import { VideoBackground } from "@/components/ui/video-background";

export const metadata: Metadata = { title: "Telephony — Butaq", description: "Call Butaq at +1 (201) 231-1497 or start a conversation in your browser with Voice." };

export default function TelephonyPage() {
  return (
    <div className={`velorah ${inter.variable} ${instrumentSerif.variable} relative isolate min-h-svh bg-background text-foreground antialiased`} style={{ fontFamily: "var(--font-velorah-sans), sans-serif" }}>
      <VelorahStyles />
      <VideoBackground
        src="/media/telephony-asme.mp4"
        poster="/media/telephony-asme.jpg"
        className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-cover bg-center after:absolute after:inset-0 after:bg-black/40"
        controlClassName="absolute bottom-6 right-6 z-20 grid size-10 cursor-pointer place-items-center rounded-full border border-white/30 bg-black/50 text-white hover:bg-black/70"
      />
      <Navbar />
      <main className="mx-auto flex max-w-5xl flex-col items-center px-5 pb-24 pt-8 text-center sm:pt-12">
        <p className="mb-4 text-xs uppercase tracking-[0.25em] text-muted-foreground">Butaq · Telephony</p>
        <h1 className="text-5xl tracking-tight [font-family:var(--font-velorah-serif)] sm:text-7xl">Telephony</h1>
        <div className="mt-16 flex w-full max-w-md flex-col items-center rounded-3xl border border-white/15 bg-black/60 p-8 backdrop-blur-xl sm:mt-20 sm:p-10">
          <Phone className="mb-6 size-8 text-white/80" strokeWidth={1.25} aria-hidden="true" />
          <h2 className="whitespace-nowrap text-xl font-medium sm:text-2xl">+1 (201) 231-1497</h2>
          <p className="mt-3 text-sm leading-relaxed text-white/70">Call and speak in Russian or Kazakh.</p>
          <a href="tel:+12012311497" className="liquid-glass mt-8 rounded-full px-8 py-3 text-sm text-white transition-colors hover:bg-white/10 motion-reduce:transition-none">Call Butaq</a>
          <a href="/voice/" className="mt-5 text-sm text-white/70 underline underline-offset-4 hover:text-white">Open Voice</a>
        </div>
      </main>
    </div>
  );
}
