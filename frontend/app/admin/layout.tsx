import type { ReactNode } from "react";
import type { Metadata } from "next";
import { inter, instrumentSerif } from "@/components/templates/velorah/fonts";
import "./admin.css";

export const metadata: Metadata = { title: "Admin — Butaq", description: "Butaq Voice Router control room" };

export default function AdminLayout({ children }: { children: ReactNode }) {
  return <div className={`admin ${inter.variable} ${instrumentSerif.variable}`}>{children}</div>;
}
