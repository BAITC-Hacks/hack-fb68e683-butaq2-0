import { HlsVideo } from "./hls-video";

const STATEMENT_HLS =
  "https://stream.mux.com/9njY8qDfS02Uvbll018C8CK39p5EksK7mn02DDC1zYvppI.m3u8";

const STATS = [
  { value: "Scenario", label: "The selected path" },
  { value: "Reason", label: "Why it fits" },
  { value: "Alternatives", label: "Other options considered" },
  { value: "Timing", label: "Time spent at each stage" },
];

export function Statement() {
  return (
    <section id="insights" className="relative flex min-h-[90vh] flex-col items-center justify-center overflow-hidden px-6">
      <HlsVideo src={STATEMENT_HLS} />

      <div className="relative z-10 flex max-w-5xl flex-col items-center text-center">
        <p className="mb-6 text-xs uppercase tracking-[0.3em] text-muted-foreground sm:text-sm">
          After every turn
        </p>

        <h2 className="text-4xl leading-[1.05] tracking-[-1.5px] text-foreground [font-family:var(--font-velorah-serif)] sm:text-6xl md:text-7xl">
          See what was chosen.
          <br />
          Understand why.
        </h2>

        <p className="mt-8 max-w-2xl text-base leading-relaxed text-muted-foreground sm:text-lg">
          A language model selects the scenario using your question and conversation
          history. The panel below the conversation lets you review its choice
          and see where processing time was spent.
        </p>

        <div className="mt-14 grid grid-cols-2 gap-8 lg:grid-cols-4 lg:gap-12">
          {STATS.map((stat) => (
            <div key={stat.label}>
              <div className="text-2xl font-light text-foreground [font-family:var(--font-velorah-serif)] sm:text-3xl">
                {stat.value}
              </div>
              <div className="text-xs text-muted-foreground sm:text-sm">
                {stat.label}
              </div>
            </div>
          ))}
        </div>

        <a
          href="/voice/"
          className="liquid-glass mt-12 rounded-full px-10 py-4 text-sm text-foreground transition-transform hover:scale-[1.03]"
        >
          Try it and see the result
        </a>
      </div>
    </section>
  );
}
