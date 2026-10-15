"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { BookMarked, Loader2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/store/auth";

type Skill = { name: string; description: string; argument_hint: string; user_invocable: boolean; context: string };

export function CodeSkillPicker({ projectId, disabled, onChoose }: {
  projectId: string; disabled: boolean; onChoose: (name: string) => void;
}) {
  const owner = useAuth(state => state.user?.id);
  const [open, setOpen] = useState(false);
  const data = useQuery({ queryKey: ["code-skills", owner, projectId], enabled: open,
    queryFn: () => api.get<{ skills: Skill[]; errors: Record<string, string> }>(`/projects/${projectId}/skills`),
    retry: false, staleTime: 0 });
  const skills = data.data?.skills.filter(skill => skill.user_invocable) || [];
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="relative text-xs">
    <summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-lg px-2 py-1.5 text-neutral-400 hover:bg-ink-700/40 [&::-webkit-details-marker]:hidden"><BookMarked className="h-3.5 w-3.5" />Skills</summary>
    <div className="absolute bottom-full left-0 z-30 mb-2 max-h-72 w-72 space-y-1 overflow-auto rounded-xl border border-ink-700 bg-ink-900 p-2 shadow-xl">
      {data.isPending && <p className="flex gap-2 p-2 text-neutral-500"><Loader2 className="h-3 w-3 animate-spin" />Загрузка skills…</p>}
      {data.isError && <p role="alert" className="p-2 text-red-300">Не удалось загрузить skills</p>}
      {data.data && !skills.length && <p className="p-2 leading-5 text-neutral-500">Создайте .layla/skills/имя/SKILL.md, затем вызовите /имя в чате.</p>}
      {skills.map(skill => <button key={skill.name} disabled={disabled} onClick={() => { onChoose(skill.name); setOpen(false); }} className="block w-full rounded-lg p-2 text-left hover:bg-ink-700/50">
        <span className="text-neutral-200">/{skill.name} <span className="text-neutral-500">{skill.argument_hint}</span></span>
        {skill.description && <span className="mt-1 block text-neutral-500">{skill.description}</span>}
        {skill.context === "fork" && <span className="mt-1 block text-accent-300">Отдельный воркер</span>}
      </button>)}
      {data.data && Object.entries(data.data.errors).map(([name, error]) => <p key={name} className="p-2 text-amber-300">{name}: {error}</p>)}
    </div>
  </details>;
}
