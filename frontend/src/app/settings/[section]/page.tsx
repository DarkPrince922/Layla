"use client";

import { notFound } from "next/navigation";
import { SETTINGS_SECTIONS } from "@/lib/settings-nav";
import { ProvidersSettings } from "@/components/ProvidersSettings";
import { PersonasSettings } from "@/components/PersonasSettings";
import { AccountsLab } from "@/components/AccountsLab";
import { KnowledgeSettings } from "@/components/KnowledgeSettings";
import { IntegrationsSettings } from "@/components/IntegrationsSettings";
import { AgentSettings } from "@/components/AgentSettings";
import { AppearanceSettings } from "@/components/AppearanceSettings";
import { GeneralSettings } from "@/components/GeneralSettings";
import { SecuritySettings } from "@/components/SecuritySettings";
import { UsersSettings } from "@/components/UsersSettings";

export default function SettingsSectionPage({ params }: { params: { section: string } }) {
  const section = SETTINGS_SECTIONS.find((s) => s.slug === params.section);
  if (!section) notFound();

  if (section.slug === "providers") return <ProvidersSettings />;
  if (section.slug === "personas") return <PersonasSettings />;
  if (section.slug === "accounts-lab") return <AccountsLab />;
  if (section.slug === "knowledge") return <KnowledgeSettings />;
  if (section.slug === "integrations") return <IntegrationsSettings />;
  if (section.slug === "agent") return <AgentSettings />;
  if (section.slug === "appearance") return <AppearanceSettings />;
  if (section.slug === "general") return <GeneralSettings />;
  if (section.slug === "security") return <SecuritySettings />;
  if (section.slug === "users") return <UsersSettings />;

  // Заглушка для разделов, реализуемых в следующих этапах.
  return (
    <div>
      <h1 className="text-xl font-semibold">{section.label}</h1>
      <p className="mt-1 text-sm text-neutral-500">{section.blurb}</p>
      <div className="surface-card mt-6 py-12 text-center text-sm text-neutral-400">
        Настройки раздела «{section.label}» пока недоступны.
      </div>
    </div>
  );
}
