import { Code2, Crosshair, Search, Palette, type LucideIcon } from "lucide-react";

export interface DomainDef {
  slug: string;
  label: string;
  icon: LucideIcon;
  color: string;
  tagline: string;
  tabs: string[];
}

export const DOMAINS: DomainDef[] = [
  {
    slug: "code",
    label: "Code",
    icon: Code2,
    color: "#3b82f6",
    tagline: "Chat agent with access to project files and repositories.",
    tabs: ["Chat", "Files", "Repos"],
  },
  {
    slug: "pentest",
    label: "Pentest",
    icon: Crosshair,
    color: "#ef4444",
    tagline: "Authorized engagements: scope, findings, reports, agent.",
    tabs: ["Overview", "Findings", "Reports", "Servers", "Activity", "Agent"],
  },
  {
    slug: "osint",
    label: "OSINT",
    icon: Search,
    color: "#06b6d4",
    tagline: "Open-source recon cases via intelligence APIs (passive).",
    tabs: ["Cases", "Timeline", "Sources"],
  },
  {
    slug: "design",
    label: "Design",
    icon: Palette,
    color: "#ec4899",
    tagline: "Generate UI artifacts (HTML/React/Vue) from a brief.",
    tabs: ["Brief", "Preview", "Code", "Design system"],
  },
];

export const domainBySlug = (slug: string) => DOMAINS.find((d) => d.slug === slug);
