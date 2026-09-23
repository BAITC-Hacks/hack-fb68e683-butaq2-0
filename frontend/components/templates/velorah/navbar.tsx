import { Wordmark } from "./primitives";

const NAV_LINKS = [
  { label: "Home", href: "/#home" },
  { label: "Why Butaq", href: "/#about" },
  { label: "Voice", href: "/voice/" },
  { label: "Insights", href: "/#insights" },
  { label: "Admin", href: "/admin/" },
];

export function Navbar() {
  return (
    <nav className="relative z-10 mx-auto flex max-w-7xl items-center justify-between px-8 py-6">
      <a href="/" aria-label="Butaq home"><Wordmark className="text-3xl" /></a>

      <div className="hidden items-center gap-10 text-sm text-white md:flex">
        {NAV_LINKS.map((link) => (
          <a
            key={link.label}
            href={link.href}
            className="text-white transition-colors hover:text-white/80"
          >
            {link.label}
          </a>
        ))}
      </div>

      <a
        href="/voice/"
        className="liquid-glass rounded-full px-6 py-2.5 text-sm text-foreground transition-transform hover:scale-[1.03]"
      >
        Talk to Butaq
      </a>
    </nav>
  );
}
