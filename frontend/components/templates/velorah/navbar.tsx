"use client";

import { usePathname } from "next/navigation";
import { Wordmark } from "./primitives";

const NAV_LINKS = [
  { label: "Voice", href: "/voice/" },
  { label: "Admin", href: "/admin/" },
];

export function Navbar() {
  const pathname = usePathname();

  return (
    <nav aria-label="Main navigation" className="relative z-10 mx-auto flex min-h-24 max-w-7xl flex-wrap items-center justify-between gap-x-4 gap-y-3 px-6 py-5 sm:px-8">
      <a href="/" aria-label="Butaq home" className="shrink-0"><Wordmark className="text-3xl" /></a>

      <div className="flex items-center gap-1 text-sm text-white sm:gap-3">
        {NAV_LINKS.map((link) => (
          <a
            key={link.label}
            href={link.href}
            aria-current={pathname === link.href || pathname.startsWith(link.href) || `${pathname}/` === link.href ? "page" : undefined}
            className="rounded-full px-4 py-3 text-white/80 transition-colors hover:bg-white/5 hover:text-white aria-[current=page]:bg-white/10 aria-[current=page]:text-white motion-reduce:transition-none"
          >
            {link.label}
          </a>
        ))}
      </div>

      <a
        href="/voice/"
        className="liquid-glass hidden rounded-full px-6 py-3 text-sm text-foreground transition-colors hover:bg-white/10 motion-reduce:transition-none sm:inline-flex"
      >
        Talk to Butaq
      </a>
    </nav>
  );
}
