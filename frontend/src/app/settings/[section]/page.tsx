"use client";

import { notFound } from "next/navigation";
import { SETTINGS_SECTIONS } from "@/lib/settings-nav";
import { ProvidersSettings } from "@/components/ProvidersSettings";
import { PersonasSettings } from "@/components/PersonasSettings";

export default function SettingsSectionPage({ params }: { params: { section: string } }) {
  const section = SETTINGS_SECTIONS.find((s) => s.slug === params.section);
  if (!section) notFound();

  if (section.slug === "providers") return <ProvidersSettings />;
  if (section.slug === "personas") return <PersonasSettings />;

  // Placeholder for sections implemented in later milestones.
  return (
    <div>
      <h1 className="text-xl font-semibold">{section.label}</h1>
      <p className="mt-1 text-sm text-neutral-500">{section.blurb}</p>
      <div className="mt-6 rounded-lg border border-dashed border-ink-700 p-8 text-center text-sm text-neutral-600">
        The <span className="text-neutral-400">{section.label}</span> section is
        part of the Layla shell (M0). Its controls are wired up in a later
        milestone.
      </div>
    </div>
  );
}
