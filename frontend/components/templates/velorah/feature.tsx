"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

const FEATURE_VIDEO =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260314_131748_f2ca2a28-fed7-44c8-b9a9-bd9acdd5ec31.mp4";

const FEATURE_TABS = [
  {
    id: "voice",
    label: "Natural speech",
    heading: "Speak your way",
    description:
      "A voice-first experience for insurance contact centres. Speak into the microphone and hear a reply, with text available whenever you need it.",
    progress: 20,
  },
  {
    id: "routing",
    label: "LLM routing",
    heading: "Meaning guides the route",
    description:
      "The routing design uses an LLM to interpret your request against a catalogue of 40 scenarios, their boundaries and the conversation so far.",
    progress: 40,
  },
  {
    id: "context",
    label: "Context",
    heading: "A new topic. Same conversation.",
    description:
      "Ask about a payment, then switch to a policy question. Butaq is designed to keep unfinished topics in context and return to them when you are ready.",
    progress: 60,
  },
  {
    id: "languages",
    label: "RU / KZ",
    heading: "Two languages. One dialogue.",
    description:
      "Russian, Kazakh, or both within a sentence. Mixed-language understanding is central to the Voice Router brief, so clients can use the words that come naturally.",
    progress: 80,
  },
  {
    id: "handoff",
    label: "Human support",
    heading: "Know when to ask for help",
    description:
      "When a request is unclear, ask a follow-up. When it needs a person, hand over with context. Irreversible actions require the client’s confirmation.",
    progress: 100,
  },
];

export function Feature({
  videoSrc = FEATURE_VIDEO,
  posterSrc,
}: {
  videoSrc?: string;
  posterSrc?: string;
}) {
  const [activeId, setActiveId] = React.useState(FEATURE_TABS[0].id);
  const [videoFailed, setVideoFailed] = React.useState(false);

  const active =
    FEATURE_TABS.find((tab) => tab.id === activeId) ?? FEATURE_TABS[0];

  return (
    <section id="routing" className="mx-auto max-w-7xl px-6 py-0 md:px-12">
      <div className="grid min-h-[520px] gap-4 overflow-hidden rounded-2xl md:grid-cols-2">
        <div className="flex flex-col justify-between rounded-2xl bg-card p-10 md:p-14">
          <div>
            <span className="mb-8 inline-block h-8 w-8 rounded-full border border-border" />
            <h2 className="mb-6 text-3xl tracking-[-1px] text-foreground [font-family:var(--font-velorah-serif)] sm:text-5xl">
              {active.heading}
            </h2>
            <p className="max-w-sm text-sm leading-relaxed text-muted-foreground sm:text-base">
              {active.description}
            </p>
          </div>

          <div>
            <div className="mb-8 flex flex-wrap gap-2">
              {FEATURE_TABS.map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setActiveId(tab.id)}
                  aria-pressed={tab.id === active.id}
                  className={cn(
                    "rounded-full border px-4 py-2 text-xs transition-colors",
                    tab.id === active.id
                      ? "border-foreground bg-foreground text-primary-foreground"
                      : "border-border text-muted-foreground hover:text-foreground",
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            <div className="mb-6 h-0.5 w-full rounded-full bg-border">
              <div
                className="h-full rounded-full bg-foreground transition-[width] duration-500 ease-out"
                style={{ width: `${active.progress}%` }}
              />
            </div>

            <a
              href="#insights"
              className="liquid-glass rounded-full px-8 py-3 text-sm text-foreground transition-transform hover:scale-[1.03]"
            >
              See the decision layer
            </a>
          </div>
        </div>

        <div className="relative min-h-[400px] overflow-hidden rounded-2xl">
          {videoFailed ? (
            <div
              aria-hidden="true"
              className="absolute inset-0 bg-gradient-to-br from-muted via-card to-background bg-cover bg-center"
              style={
                posterSrc ? { backgroundImage: `url(${posterSrc})` } : undefined
              }
            />
          ) : (
            <video
              className="absolute inset-0 h-full w-full object-cover"
              src={videoSrc}
              poster={posterSrc}
              autoPlay
              loop
              muted
              playsInline
              aria-hidden="true"
              tabIndex={-1}
              onError={() => setVideoFailed(true)}
            />
          )}
        </div>
      </div>
    </section>
  );
}
