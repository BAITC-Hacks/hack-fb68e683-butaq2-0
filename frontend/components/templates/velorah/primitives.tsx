import { cn } from "@/lib/utils";

/**
 * Butaq wordmark set in
 * Instrument Serif. The mark scales with the wrapper font size, so the same
 * component serves the navbar (text-3xl) and the footer (text-xl).
 */
export function Wordmark({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "tracking-tight text-foreground [font-family:var(--font-velorah-serif)]",
        className,
      )}
    >
      Butaq
    </span>
  );
}
