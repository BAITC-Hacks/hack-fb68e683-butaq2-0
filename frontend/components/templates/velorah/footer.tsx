import { Wordmark } from "./primitives";

const FOOTER_LINKS = [
  { label: "Why Butaq", href: "#about" },
  { label: "Voice", href: "/voice/" },
  { label: "Supervisor insights", href: "#insights" },
  { label: "The project", href: "#project" },
];

export function Footer() {
  return (
    <footer className="mx-auto max-w-7xl border-t border-border bg-[hsl(0,0%,0%)] px-6 py-16 md:px-12">
      <div className="mb-16 grid grid-cols-1 gap-12 md:grid-cols-3">
        <h2 className="text-2xl leading-tight text-foreground [font-family:var(--font-velorah-serif)] sm:text-3xl">
          Your voice.
          <br />
          Understood in context.
        </h2>

        <nav className="flex flex-col items-start gap-3">
          {FOOTER_LINKS.map((link) => (
            <a
              key={link.href}
              href={link.href}
              className="text-sm capitalize text-muted-foreground transition-colors hover:text-foreground"
            >
              {link.label}
            </a>
          ))}
        </nav>

        <div>
          <p className="mb-4 text-sm text-muted-foreground">
            Built for natural conversations.
            <br />
            Designed for human trust.
          </p>
          <a
            href="#home"
            className="liquid-glass rounded-full px-6 py-2.5 text-sm text-foreground transition-transform hover:scale-[1.03]"
          >
            Back to the beginning
          </a>
        </div>
      </div>

      <div className="flex flex-col items-center justify-between gap-4 border-t border-border pt-8 text-xs text-muted-foreground md:flex-row">
        <Wordmark className="text-xl" />
        <div className="flex items-center gap-6">
          <span>HackAlem AI · Voice Router</span>
          <span>Frontend preview</span>
        </div>
      </div>
    </footer>
  );
}
