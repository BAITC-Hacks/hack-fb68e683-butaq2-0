import type { ReactNode } from "react";
import type { Metadata } from "next";
import { inter, instrumentSerif } from "@/components/templates/velorah/fonts";
import { Navbar } from "@/components/templates/velorah/navbar";
import { VelorahStyles } from "@/components/templates/velorah/styles";
import "./admin.css";

export const metadata: Metadata = { title: "Admin — Butaq", description: "Butaq Voice Router control room" };

export default function AdminLayout({ children }: { children: ReactNode }) {
  return <div className={`velorah admin ${inter.variable} ${instrumentSerif.variable}`}><VelorahStyles /><header className="admin-site-header"><Navbar /></header>{children}</div>;
}
