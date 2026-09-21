"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { GitBranch, Plus, FolderGit2 } from "lucide-react";
import { api, type Project } from "@/lib/api";
import { FileTree } from "@/components/FileTree";
import { ChatPanel } from "@/components/ChatPanel";

export function CodeDomain() {
  const qc = useQueryClient();
  const [projectId, setProjectId] = useState<string | null>(null);
  const [openFile, setOpenFile] = useState<{ path: string; content: string } | null>(null);
  const [importing, setImporting] = useState(false);
  const [repoUrl, setRepoUrl] = useState("");
  const [importErr, setImportErr] = useState<string | null>(null);

  const { data: projects = [] } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });

  useEffect(() => {
    if (!projectId && projects.length) setProjectId(projects[0].id);
  }, [projects, projectId]);

  const importRepo = useMutation({
    mutationFn: () => api.post<Project>("/projects/import", { repo_url: repoUrl }),
    onSuccess: (p) => {
      qc.invalidateQueries({ queryKey: ["projects"] });
      setProjectId(p.id);
      setRepoUrl("");
      setImporting(false);
      setImportErr(null);
    },
    onError: (e) => setImportErr(e instanceof Error ? e.message : "Не удалось импортировать"),
  });

  async function openPath(path: string) {
    const f = await api.get<{ path: string; content: string }>(
      `/projects/${projectId}/file?path=${encodeURIComponent(path)}`,
    );
    setOpenFile(f);
  }

  return (
    <div className="flex h-full">
      {/* Левая колонка: проекты + дерево файлов */}
      <div className="flex w-64 shrink-0 flex-col border-r border-ink-700 bg-ink-900">
        <div className="flex items-center justify-between border-b border-ink-700 px-3 py-2">
          <span className="text-xs font-semibold uppercase tracking-wide text-neutral-500">
            Проекты
          </span>
          <button
            onClick={() => setImporting((v) => !v)}
            className="text-neutral-400 hover:text-neutral-200"
            aria-label="Импортировать репозиторий"
          >
            <Plus className="h-4 w-4" />
          </button>
        </div>

        {importing && (
          <div className="space-y-2 border-b border-ink-700 p-3">
            <input
              value={repoUrl}
              onChange={(e) => setRepoUrl(e.target.value)}
              placeholder="https://github.com/user/repo.git"
              className="w-full rounded-md border border-ink-700 bg-ink-800 px-2 py-1.5 text-xs"
            />
            {importErr && <p className="text-[11px] text-red-400">{importErr}</p>}
            <button
              onClick={() => importRepo.mutate()}
              disabled={!repoUrl || importRepo.isPending}
              className="flex w-full items-center justify-center gap-1.5 rounded-md bg-indigo-600 px-2 py-1.5 text-xs text-white disabled:opacity-50"
            >
              <GitBranch className="h-3.5 w-3.5" />
              {importRepo.isPending ? "Клонирование…" : "Импортировать репозиторий"}
            </button>
          </div>
        )}

        <div className="border-b border-ink-700 p-2">
          {projects.length === 0 ? (
            <p className="px-1 py-2 text-xs text-neutral-600">
              Пока нет проектов. Импортируйте репозиторий, чтобы начать.
            </p>
          ) : (
            projects.map((p) => (
              <button
                key={p.id}
                onClick={() => {
                  setProjectId(p.id);
                  setOpenFile(null);
                }}
                className={`flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs ${
                  projectId === p.id ? "bg-ink-700 text-white" : "text-neutral-300 hover:bg-ink-800"
                }`}
              >
                <FolderGit2 className="h-3.5 w-3.5 shrink-0 text-neutral-500" />
                <span className="truncate">{p.name}</span>
              </button>
            ))
          )}
        </div>

        <div className="flex-1 overflow-y-auto">
          {projectId && <FileTree projectId={projectId} onOpen={openPath} />}
        </div>
      </div>

      {/* Центр: просмотр файла */}
      <div className="flex min-w-0 flex-1 flex-col border-r border-ink-700">
        {openFile ? (
          <>
            <div className="border-b border-ink-700 bg-ink-900 px-4 py-2 text-xs text-neutral-400">
              {openFile.path}
            </div>
            <pre className="flex-1 overflow-auto p-4 text-xs leading-relaxed text-neutral-300">
              <code>{openFile.content}</code>
            </pre>
          </>
        ) : (
          <div className="grid flex-1 place-items-center p-6 text-center text-sm text-neutral-600">
            Выберите файл в дереве слева, чтобы просмотреть его.
          </div>
        )}
      </div>

      {/* Правая колонка: чат-агент домена Код */}
      <div className="flex w-[420px] shrink-0 flex-col">
        <ChatPanel domain="code" />
      </div>
    </div>
  );
}
