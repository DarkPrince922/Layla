import { notFound } from "next/navigation";
import { domainBySlug, DOMAINS } from "@/lib/domains";

export function generateStaticParams() {
  return DOMAINS.map((d) => ({ domain: d.slug }));
}

export default function DomainPage({ params }: { params: { domain: string } }) {
  const domain = domainBySlug(params.domain);
  if (!domain) notFound();
  const Icon = domain.icon;

  return (
    <div className="flex h-full flex-col">
      {/* Top bar: domain tabs (spec §3). */}
      <header className="flex items-center gap-4 border-b border-ink-700 bg-ink-900 px-6 py-3">
        <div className="flex items-center gap-2">
          <Icon className="h-5 w-5" style={{ color: domain.color }} />
          <h1 className="text-sm font-semibold">{domain.label}</h1>
        </div>
        <nav className="flex gap-1 text-xs">
          {domain.tabs.map((t, i) => (
            <button
              key={t}
              className={`rounded px-2.5 py-1 ${
                i === 0 ? "bg-ink-700 text-white" : "text-neutral-400 hover:bg-ink-800"
              }`}
            >
              {t}
            </button>
          ))}
        </nav>
        <div className="ml-auto text-[11px] text-neutral-500">⌘K</div>
      </header>

      {/* Placeholder body — domain features land in later milestones. */}
      <div className="grid flex-1 place-items-center p-8">
        <div className="max-w-md text-center">
          <div
            className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-xl"
            style={{ backgroundColor: `${domain.color}22` }}
          >
            <Icon className="h-6 w-6" style={{ color: domain.color }} />
          </div>
          <h2 className="text-lg font-medium text-neutral-200">{domain.label} domain</h2>
          <p className="mt-1 text-sm text-neutral-500">{domain.tagline}</p>
          <p className="mt-4 text-xs text-neutral-600">
            This is the M0 shell. Domain features are implemented in later
            milestones (chat &amp; providers in M1).
          </p>
        </div>
      </div>
    </div>
  );
}
