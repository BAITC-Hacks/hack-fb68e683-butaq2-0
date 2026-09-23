const CTA_VIDEO =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260306_074215_04640ca7-042c-45d6-bb56-58b1e8a42489.mp4";

export function Cta() {
  return (
    <section id="project" className="relative flex min-h-[90vh] flex-col items-center justify-center overflow-hidden px-6 text-center">
      <video
        className="absolute inset-0 z-0 h-full w-full object-cover"
        src={CTA_VIDEO}
        autoPlay
        loop
        muted
        playsInline
        aria-hidden="true"
        tabIndex={-1}
      />

      <div className="relative z-10 flex max-w-4xl flex-col items-center">
        <p className="mb-4 text-xs uppercase tracking-[0.3em] text-muted-foreground sm:text-sm">
          HackAlem AI · Halyk Bank track · Case 2
        </p>

        <h2 className="text-5xl leading-[0.95] tracking-[-2px] text-foreground [font-family:var(--font-velorah-serif)] sm:text-7xl md:text-8xl">
          A better conversation starts here.
        </h2>

        <p className="mt-6 max-w-xl text-base leading-relaxed text-muted-foreground sm:text-lg">
          Butaq is our Voice Router concept: an LLM decision layer for natural
          conversations, with clear explanations and human support when it matters.
          Start a voice conversation and follow each routing decision as it happens.
        </p>

        <div className="mt-10 flex flex-col items-center gap-4 sm:flex-row">
          <a
            href="/voice/"
            className="liquid-glass rounded-full px-10 py-4 text-sm text-foreground transition-transform hover:scale-[1.03]"
          >
            Talk to Butaq
          </a>
          <a
            href="#insights"
            className="rounded-full border border-border px-10 py-4 text-sm text-muted-foreground transition-colors hover:border-foreground/30 hover:text-foreground"
          >
            View routing goals
          </a>
        </div>
      </div>
    </section>
  );
}
