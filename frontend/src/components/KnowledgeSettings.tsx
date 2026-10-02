"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2, Search, FileText, Upload } from "lucide-react";
import { api, type KnowledgeDoc, type SearchHit } from "@/lib/api";

const DOMAINS = ["", "code", "pentest", "osint", "design"];

export function KnowledgeSettings() {
  const qc = useQueryClient();
  const [title, setTitle] = useState("");
  const [content, setContent] = useState("");
  const [domain, setDomain] = useState("");
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<SearchHit[] | null>(null);

  const { data: docs = [] } = useQuery({
    queryKey: ["knowledge"],
    queryFn: () => api.get<KnowledgeDoc[]>("/knowledge"),
  });

  const upload = useMutation({
    mutationFn: () =>
      api.post("/knowledge", { title, content, domain: domain || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["knowledge"] });
      setTitle("");
      setContent("");
    },
  });
  const remove = useMutation({
    mutationFn: (id: string) => api.del(`/knowledge/${id}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["knowledge"] }),
  });
  const search = useMutation({
    mutationFn: () => api.post<SearchHit[]>("/knowledge/search", { query, top_k: 5 }),
    onSuccess: (r) => setHits(r),
  });

  return (
    <div>
      <h1 className="text-xl font-semibold">База знаний</h1>
      <p className="mb-4 text-sm text-neutral-500">
        Загрузите документы — они нарезаются на чанки и индексируются для RAG.
        Поиск по смыслу (косинусное сходство эмбеддингов).
      </p>

      <div className="mb-6 space-y-2 rounded-xl border border-ink-700/70 bg-ink-800/30 p-5">
        <div className="flex gap-2">
          <input
            value={title}
            onChange={(e) => setTitle(e.target.value)}
            placeholder="Название документа"
            className="flex-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-sm"
          />
          <select
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            className="rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-sm"
          >
            {DOMAINS.map((d) => (
              <option key={d} value={d}>
                {d || "любой домен"}
              </option>
            ))}
          </select>
        </div>
        <textarea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          rows={5}
          placeholder="Вставьте текст документа…"
          className="w-full resize-none rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-sm"
        />
        <button
          onClick={() => upload.mutate()}
          disabled={!title || !content || upload.isPending}
          className="flex items-center gap-1.5 rounded-md bg-accent-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
        >
          <Upload className="h-4 w-4" /> {upload.isPending ? "Индексация…" : "Загрузить и проиндексировать"}
        </button>
      </div>

      <div className="mb-6 rounded-xl border border-ink-700/70 bg-ink-800/30 p-5">
        <div className="flex gap-2">
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && search.mutate()}
            placeholder="Поиск по базе знаний…"
            className="flex-1 rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-sm"
          />
          <button
            onClick={() => search.mutate()}
            disabled={!query || search.isPending}
            className="flex items-center gap-1.5 rounded-md bg-ink-700 px-3 py-1.5 text-sm text-white disabled:opacity-50"
          >
            <Search className="h-4 w-4" /> Найти
          </button>
        </div>
        {hits && (
          <div className="mt-3 space-y-2">
            {hits.length === 0 && <p className="text-xs text-neutral-600">Ничего не найдено.</p>}
            {hits.map((h) => (
              <div key={h.chunk_id} className="rounded-md border border-ink-700 bg-ink-800 p-2 text-xs">
                <div className="mb-1 text-[11px] text-neutral-500">
                  релевантность {h.score.toFixed(3)}
                </div>
                <div className="text-neutral-300">{h.content.slice(0, 300)}</div>
              </div>
            ))}
          </div>
        )}
      </div>

      <h2 className="mb-2 text-sm font-semibold text-neutral-300">Документы</h2>
      {docs.length === 0 ? (
        <p className="rounded-lg border border-dashed border-ink-700 p-6 text-center text-sm text-neutral-500">
          Пока нет документов.
        </p>
      ) : (
        <ul className="divide-y divide-ink-700 rounded-lg border border-ink-700">
          {docs.map((d) => (
            <li key={d.id} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              <FileText className="h-4 w-4 text-neutral-500" />
              <span className="flex-1 truncate">{d.title}</span>
              {d.domain && (
                <span className="rounded bg-ink-700 px-1.5 py-0.5 text-[11px] text-neutral-400">
                  {d.domain}
                </span>
              )}
              <span className="text-[11px] text-neutral-500">{d.chunk_count} чанков</span>
              <button
                onClick={() => remove.mutate(d.id)}
                className="text-neutral-500 hover:text-red-400"
                aria-label="Удалить"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
