import type { Metadata } from "next";
import type { ReactNode } from "react";
import "./globals.css";

export const metadata: Metadata = { title: "Butaq — Voice Router", description: "Context-aware voice routing for contact centres. Russian and Kazakh conversations, LLM scenario selection and explainable decisions. HackAlem AI · Case 2." };

export default function RootLayout({ children }: { children: ReactNode }) {
  return <html lang="en"><body>{children}</body></html>;
}
