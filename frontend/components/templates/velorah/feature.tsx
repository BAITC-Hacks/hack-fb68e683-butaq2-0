"use client";

import * as React from "react";

import { cn } from "@/lib/utils";

const FEATURE_VIDEO =
  "https://d8j0ntlcm91z4.cloudfront.net/user_38xzZboKViGWJOttwIXH07lWA1P/hf_20260314_131748_f2ca2a28-fed7-44c8-b9a9-bd9acdd5ec31.mp4";

const FEATURE_TABS = [
  {
    id: "voice",
    label: "1. Start",
    heading: "Open Voice",
    description:
      "Click “Talk to Butaq” and allow microphone access. Prefer typing? Use the message field instead.",
    progress: 20,
  },
  {
    id: "routing",
    label: "2. Ask",
    heading: "Describe what you need",
    description:
      "Ask about a policy, payment, or claim. Speak Russian, Kazakh, or mix both. Pause to send your turn.",
    progress: 40,
  },
  {
    id: "context",
    label: "3. Continue",
    heading: "Follow up or change topics",
    description:
      "Wait for the spoken reply, then ask your next question. Butaq considers earlier turns when choosing a scenario.",
    progress: 60,
  },
  {
    id: "languages",
    label: "4. Review",
    heading: "See the result",
    description:
      "Below the conversation, see the transcript, selected scenario, explanation, alternatives, and processing times.",
    progress: 80,
  },
  {
    id: "handoff",
    label: "When uncertain",
    heading: "Clarify before deciding",
    description:
      "When details are missing, Butaq asks a follow-up or flags that an operator is needed. This demo does not connect to a live operator.",
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
              Understand the result
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
