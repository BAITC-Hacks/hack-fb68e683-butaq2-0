import type { Metadata } from "next";
import Velorah from "@/components/templates/velorah/velorah";

export const metadata: Metadata = { title: "Butaq — Voice assistant for insurance", description: "Ask an insurance question in Russian or Kazakh. Butaq replies by voice and shows the selected scenario, explanation, and processing times." };

export default function Page() {
  return <Velorah />;
}
