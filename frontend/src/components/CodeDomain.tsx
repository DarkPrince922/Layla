"use client";

import { useAuth } from "@/store/auth";
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download,
  MessageCircle,
  FileCode2,
  Sparkles,
  FilePlus2,
  Folder,
  FolderGit2,
  GitBranch,
  GitCommitHorizontal,
  MonitorPlay,
  Play,
  Plus,
  Save,
  SquareTerminal,
  Trash2,
} from "lucide-react";
import {
  api,
  downloadProject,
  type FileChange,
  type FileContent,
  type Project,
} from "@/lib/api";
import { FileTree } from "@/components/FileTree";
import { FileDiff } from "@/components/FileDiff";
import { ChatPanel } from "@/components/ChatPanel";
import { ProjectTerminal, type RunRequest } from "@/components/ProjectTerminal";
import { GitPanel } from "@/components/GitPanel";
import { PreviewPanel } from "@/components/PreviewPanel";
import { confirmAction } from "@/components/ConfirmDialog";

type OpenFile = Omit<FileContent, "sha256"> & { sha256: string | null };
type Pane = "projects" | "editor" | "chat" | "terminal" | "git" | "preview";
const button =
  "secondary-button px-3 text-xs";

export function CodeDomain() {
  const qc = useQueryClient();
  const owner = useAuth(s => s.user?.id);
  const projectKey = `layla:project:${owner}`;
  const [projectId, setProjectId] = useState<string | null>(null);
  const [pane, setPane] = useState<Pane>("chat");
  const [form, setForm] = useState<"local" | "import" | null>(null);
  const [name, setName] = useState("");
  const [repoUrl, setRepoUrl] = useState("");
  const [openFile, setOpenFile] = useState<OpenFile | null>(null);
  const [draft, setDraft] = useState("");
  const [newPath, setNewPath] = useState("");
  const [addingFile, setAddingFile] = useState(false);
  const [change, setChange] = useState<FileChange | null>(null);
  const [stale, setStale] = useState(false);
  const [pending, setPending] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runRequest, setRunRequest] = useState<RunRequest | null>(null);
  // Превью монтируется при первом открытии и дальше живёт (iframe не перезагружается при смене панели).
  const [previewOpened, setPreviewOpened] = useState(false);
  const [filesChanged, setFilesChanged] = useState(0);
  const openSequence = useRef(0);
  const dirty =
    !!openFile && (openFile.sha256 === null || draft !== openFile.content);
  const { data: projects = [], error: projectsError, isPending: loadingProjects, isFetching: refreshingProjects } = useQuery({
    queryKey: ["projects"],
    queryFn: () => api.get<Project[]>("/projects"),
  });
  const project = projects.find((p) => p.id === projectId);

  useEffect(() => {
    if (!projectId && projects.length) {
      const linked = new URLSearchParams(window.location.search).get("project");
      // Ссылка на проект, которого нет в (возможно, устаревшем) списке, — ждём свежий
      // список, а не открываем первый попавшийся («Открыть в Коде» из Дизайна).
      if (linked && !projects.some(p => p.id === linked) && refreshingProjects) return;
      let stored = ""; try { stored = localStorage.getItem(projectKey) || ""; } catch {}
      setProjectId(projects.find(p => p.id === linked || p.id === stored)?.id || projects[0].id);
    }
  }, [projects, projectId, projectKey, refreshingProjects]);
  useEffect(() => { if (projectId) try { localStorage.setItem(projectKey, projectId); } catch {} }, [projectId, projectKey]);
  useEffect(() => { if (pane === "preview") setPreviewOpened(true); }, [pane]);
  useEffect(() => { setPreviewOpened(false); setFilesChanged(0); }, [projectId]);
  useEffect(() => {
    const open = (event: Event) => {
      const target = (event as CustomEvent).detail;
      if (target.domain !== "code") return;
      const id = target.project_id || null;
      // Смена проекта должна сбрасывать открытый файл/черновик — иначе «Сохранить»
      // ушло бы в новый проект (и незавершённый openPath показал бы чужой файл).
      if (id && id !== projectId) selectProject(id);
      setPane("chat");
    };
    window.addEventListener("layla:open-chat", open);
    return () => window.removeEventListener("layla:open-chat", open);
  });
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  function canLeave() {
    return (
      !pending &&
      (!dirty || window.confirm("Отменить несохранённые изменения файла?"))
    );
  }
  function selectProject(id: string) {
    if (id === projectId || !canLeave()) return;
    openSequence.current++;
    setProjectId(id);
    setOpenFile(null);
    setDraft("");
    setChange(null);
    setStale(false);
    setError(null);
  }
  // Удаляет проект целиком: файлы на сервере и чаты проекта (бэкенд стирает папку).
  async function removeProject(target: Project) {
    if (pending) return;
    // Одно окно подтверждения на всё (без системного confirm для несохранённого файла).
    const unsaved = target.id === projectId && dirty ? "\nНесохранённые изменения открытого файла тоже пропадут." : "";
    if (!(await confirmAction(`Удалить проект «${target.name}»? Проект вместе с чатами попадёт в корзину: 7 дней его можно восстановить (Настройки → Корзина), потом файлы сотрутся.${unsaved}`))) return;
    setPending(true);
    setError(null);
    try {
      await api.del(`/projects/${target.id}`);
      qc.setQueryData<Project[]>(["projects"], (old = []) => old.filter((p) => p.id !== target.id));
      if (target.id === projectId) {
        openSequence.current++;
        setProjectId(null);
        setOpenFile(null);
        setDraft("");
        setChange(null);
        setStale(false);
        try { localStorage.removeItem(projectKey); } catch {}
      }
      await Promise.all([
        qc.invalidateQueries({ queryKey: ["projects"] }),
        qc.invalidateQueries({ queryKey: ["chats"] }),
      ]);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось удалить проект");
    } finally {
      setPending(false);
    }
  }
  async function createProject() {
    if (!canLeave()) return;
    setPending(true);
    setError(null);
    try {
      const p = await api.post<Project>(
        form === "import" ? "/projects/import" : "/projects",
        form === "import"
          ? { repo_url: repoUrl.trim(), name: name.trim() || null }
          : { name: name.trim() },
      );
      qc.setQueryData<Project[]>(["projects"], (old = []) => [...old, p]);
      openSequence.current++;
      setProjectId(p.id);
      setOpenFile(null);
      setDraft("");
      setChange(null);
      setStale(false);
      setForm(null);
      setName("");
      setRepoUrl("");
      setPane("chat");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось создать проект");
    } finally {
      setPending(false);
    }
  }
  async function openPath(path: string, force = false) {
    if (!projectId || (!force && !canLeave())) return;
    const seq = ++openSequence.current;
    setError(null);
    try {
      const file = await api.get<FileContent>(
        `/projects/${projectId}/file?path=${encodeURIComponent(path)}`,
      );
      if (seq !== openSequence.current) return;
      setOpenFile(file);
      setDraft(file.content);
      setStale(false);
      setChange(null);
      setPane("editor");
    } catch (e) {
      if (seq === openSequence.current)
        setError(e instanceof Error ? e.message : "Не удалось открыть файл");
    }
  }
  function startFile() {
    if (!newPath.trim() || !canLeave()) return;
    openSequence.current++;
    setOpenFile({ path: newPath.trim(), content: "", sha256: null });
    setDraft("");
    setNewPath("");
    setAddingFile(false);
    setChange(null);
    setStale(false);
    setPane("editor");
  }
  async function saveFile(remove = false) {
    if (!projectId || !openFile || pending) return;
    if (remove && !(await confirmAction(`Удалить файл ${openFile.path}?`))) return;
    setPending(true);
    setError(null);
    try {
      const result = remove
        ? await api.del<FileChange>(
            `/projects/${projectId}/file?path=${encodeURIComponent(openFile.path)}&expected_sha256=${openFile.sha256}`,
          )
        : await api.put<FileChange>(`/projects/${projectId}/file`, {
            path: openFile.path,
            content: draft,
            expected_sha256: openFile.sha256,
          });
      setChange(result);
      setStale(false);
      setFilesChanged(n => n + 1);
      if (remove) {
        setOpenFile(null);
        setDraft("");
      } else
        setOpenFile({
          path: result.path,
          content: draft,
          sha256: result.after_sha256,
        });
      await qc.invalidateQueries({ queryKey: ["project-files", projectId] });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось сохранить файл");
    } finally {
      setPending(false);
    }
  }
  async function agentChanged(result: FileChange) {
    setFilesChanged(n => n + 1);
    await qc.invalidateQueries({ queryKey: ["project-files", projectId] });
    if (openFile?.path === result.path) {
      // Preserve drafts; the hash guard prevents an old editor overwriting the agent.
      setStale(true);
    }
  }
  async function download() {
    if (!project) return;
    setDownloading(true);
    setError(null);
    try {
      await downloadProject(project);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Не удалось скачать ZIP");
    } finally {
      setDownloading(false);
    }
  }

  return (
    <div className="code-workspace flex h-full min-w-0 flex-col">
      <header className="workspace-toolbar">
        <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-accent-500/10 text-accent-300"><Folder className="h-5 w-5" /></span>
        <div className="workspace-title"><h1>{project?.name || "Ваше рабочее пространство"}</h1><p>{project ? project.repo_url ? "Код · Проект из Git" : "Код · Локальный проект" : "От идеи до готового проекта"}</p></div>
        {project && <button className={button} onClick={download} disabled={downloading}><Download className="h-4 w-4" /><span>{downloading ? "Сборка…" : "ZIP"}</span></button>}
      </header>
      <div className="flex shrink-0 items-center justify-between gap-2 px-4 pb-3 md:px-7">
        <nav className="segmented-control" aria-label="Панели проекта">
          {([
            ["chat", "Чат", MessageCircle],
            ["projects", "Файлы и проекты", Folder],
            ...(openFile ? [["editor", "Редактор", FileCode2] as const] : []),
            ...(projectId ? [["preview", "Превью", MonitorPlay] as const, ["terminal", "Терминал", SquareTerminal] as const, ["git", "Git", GitCommitHorizontal] as const] : []),
          ] as const).map(([id, label, Icon]) => <button key={id} aria-pressed={pane === id} onClick={() => setPane(id)} className="segment"><Icon className="hidden h-3.5 w-3.5 sm:block" />{label}</button>)}
        </nav>
        <button onClick={() => { setForm("local"); setPane("projects"); }} aria-label="Новый проект" className="icon-button"><Plus className="h-5 w-5" /></button>
      </div>
      {(error || projectsError) && (
        <div
          role="alert"
          className="flex items-center justify-between gap-2 border-b border-red-500/20 bg-red-500/10 px-4 py-2 text-xs text-red-300"
        >
          <span>{error || projectsError?.message}</span>
          <button onClick={() => setError(null)} aria-label="Закрыть ошибку">
            ×
          </button>
        </div>
      )}
      <div className={`code-panes flex min-h-0 flex-1 gap-4 ${pane === "chat" ? "" : "px-3 pb-3 md:px-5 md:pb-5"}`} data-pane={pane}>
        <aside
          className={`code-pane code-projects min-h-0 flex-col overflow-y-auto ${pane === "projects" ? "code-active" : ""}`}
        >
          <div className="flex items-center justify-between px-3 py-3">
            <span className="text-xs font-semibold text-neutral-400">
              Мои проекты
            </span>
            <button
              onClick={() => setForm(form ? null : "local")}
              aria-label="Новый проект"
              className="rounded p-1 text-neutral-300 hover:bg-ink-700"
            >
              <Plus className="h-4 w-4" />
            </button>
          </div>
          {(form || projects.length === 0) && (
            <form
              className="space-y-3 border-b border-ink-700 p-3 pt-0"
              onSubmit={(e) => {
                e.preventDefault();
                createProject();
              }}
            >
              <div className="flex rounded-lg bg-ink-800 p-1 text-xs">
                <button
                  type="button"
                  onClick={() => setForm("local")}
                  className={`flex-1 rounded p-1.5 ${form !== "import" ? "bg-ink-600" : "text-neutral-400"}`}
                >
                  С нуля
                </button>
                <button
                  type="button"
                  onClick={() => setForm("import")}
                  className={`flex-1 rounded p-1.5 ${form === "import" ? "bg-ink-600" : "text-neutral-400"}`}
                >
                  Из Git
                </button>
              </div>
              <input
                aria-label="Название проекта"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Название проекта"
                maxLength={200}
                className="w-full rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-xs"
              />
              {form === "import" && (
                <input
                  aria-label="URL репозитория"
                  value={repoUrl}
                  onChange={(e) => setRepoUrl(e.target.value)}
                  placeholder="https://github.com/user/repo"
                  className="w-full rounded-lg border border-ink-700 bg-ink-800 px-3 py-2 text-xs"
                />
              )}
              <button
                disabled={
                  pending ||
                  (form === "import" ? !repoUrl.trim() : !name.trim())
                }
                className="flex w-full items-center justify-center gap-2 rounded-lg bg-accent-600 px-3 py-2 text-xs font-medium text-white hover:bg-accent-500 disabled:opacity-40"
              >
                {form === "import" ? (
                  <GitBranch className="h-3.5 w-3.5" />
                ) : (
                  <Plus className="h-3.5 w-3.5" />
                )}
                {pending
                  ? "Создание…"
                  : form === "import"
                    ? "Импортировать"
                    : "Создать проект"}
              </button>
              <p className="text-[11px] leading-relaxed text-neutral-500">
                Файлы хранятся на вашем сервере Layla. GitHub необязателен.
              </p>
            </form>
          )}
          <div className="max-h-48 shrink-0 overflow-y-auto border-b border-ink-700/60 px-3 pb-3">
            {projects.map((p) => (
              <div key={p.id} className={`group flex items-center rounded-lg ${projectId === p.id ? "bg-accent-500/15 text-accent-200" : "text-neutral-300 hover:bg-ink-800"}`}>
                <button
                  disabled={pending}
                  onClick={() => selectProject(p.id)}
                  className="flex min-w-0 flex-1 items-center gap-2 px-2 py-2 text-left text-xs"
                >
                  {p.repo_url ? (
                    <FolderGit2 className="h-4 w-4 shrink-0" />
                  ) : (
                    <Folder className="h-4 w-4 shrink-0" />
                  )}
                  <span className="truncate">{p.name}</span>
                </button>
                <button
                  aria-label={`Удалить проект ${p.name}`}
                  title="Удалить проект"
                  disabled={pending}
                  onClick={() => removeProject(p)}
                  className="mr-1 shrink-0 rounded p-1.5 text-neutral-500 opacity-60 hover:bg-red-500/15 hover:text-red-300 group-hover:opacity-100"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </button>
              </div>
            ))}
          </div>
          {projectId && (
            <>
              <div className="flex items-center justify-between px-3 py-2">
                <span className="text-[11px] text-neutral-500">Файлы проекта</span>
                <button
                  onClick={() => setAddingFile(!addingFile)}
                  aria-label="Создать файл"
                  className="p-1 text-neutral-400"
                >
                  <FilePlus2 className="h-4 w-4" />
                </button>
              </div>
              {addingFile && (
                <form
                  className="flex gap-1 px-2 pb-2"
                  onSubmit={(e) => {
                    e.preventDefault();
                    startFile();
                  }}
                >
                  <input
                    aria-label="Путь нового файла"
                    value={newPath}
                    onChange={(e) => setNewPath(e.target.value)}
                    placeholder="src/main.py"
                    className="min-w-0 flex-1 rounded border border-ink-700 bg-ink-800 p-2 text-xs"
                  />
                  <button className={button} disabled={!newPath.trim()}>
                    OK
                  </button>
                </form>
              )}
              <div className="min-h-0 flex-1 overflow-auto">
                <FileTree
                  key={projectId}
                  projectId={projectId}
                  onOpen={openPath}
                />
              </div>
            </>
          )}
        </aside>
        <section
          className={`code-pane code-editor min-h-0 min-w-0 flex-1 flex-col ${pane === "editor" ? "code-active" : ""}`}
        >
          {openFile ? (
            <>
              <div className="flex flex-wrap items-center gap-2 border-b border-ink-700 px-3 py-2">
                <span className="min-w-0 flex-1 truncate font-mono text-xs text-neutral-300">
                  {openFile.path}
                  {dirty ? " •" : ""}
                </span>
                <button
                  onClick={() => saveFile()}
                  disabled={pending || !dirty}
                  className={button}
                >
                  <Save className="h-3.5 w-3.5" />
                  Сохранить
                </button>
                {openFile.sha256 && (
                  <button
                    onClick={() => { setRunRequest({ id: Date.now(), path: openFile.path }); setPane("terminal"); }}
                    disabled={pending || dirty}
                    title={dirty ? "Сохраните файл перед запуском" : "Запустить файл в песочнице проекта"}
                    className={button}
                  >
                    <Play className="h-3.5 w-3.5" />
                    Запустить
                  </button>
                )}
                {openFile.sha256 && (
                  <button
                    onClick={() => saveFile(true)}
                    disabled={pending}
                    className={button}
                    aria-label="Удалить файл"
                  >
                    <Trash2 className="h-3.5 w-3.5 text-red-400" />
                  </button>
                )}
              </div>
              {stale && (
                <div className="flex flex-wrap items-center gap-2 bg-amber-500/10 px-3 py-2 text-xs text-amber-300">
                  Агент изменил этот файл. Ваш черновик сохранён в редакторе.
                  <button
                    className="underline"
                    onClick={() => openPath(openFile.path)}
                  >
                    Перечитать файл
                  </button>
                </div>
              )}
              <textarea
                aria-label="Содержимое файла"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                spellCheck={false}
                disabled={pending}
                className="min-h-48 flex-1 resize-none border-0 bg-transparent p-5 font-mono text-sm leading-7 text-neutral-200 focus-visible:outline-accent-400"
              />
            </>
          ) : (
            <div className="grid flex-1 place-items-center p-8 text-center">
              <div className="max-w-xs">
                <FilePlus2 className="mx-auto mb-4 h-8 w-8 text-neutral-600" />
                <p className="text-sm text-neutral-300">Код вашего проекта</p>
                <p className="mt-2 text-xs leading-relaxed text-neutral-500">
                  Откройте файл или поручите агенту создать структуру проекта.
                </p>
              </div>
            </div>
          )}
          {change && (
            <div className="max-h-72 shrink-0 overflow-auto border-t border-ink-700 px-3">
              <FileDiff change={change} expanded />
            </div>
          )}
        </section>
        {projectId && (
          <section
            className={`code-pane code-terminal min-h-0 min-w-0 flex-1 flex-col ${pane === "terminal" ? "code-active" : ""}`}
          >
            <ProjectTerminal key={projectId} projectId={projectId} request={runRequest} />
          </section>
        )}
        {projectId && (pane === "preview" || previewOpened) && (
          <section className={`code-pane code-preview min-h-0 min-w-0 flex-1 flex-col ${pane === "preview" ? "code-active" : ""}`}>
            <PreviewPanel key={projectId} projectId={projectId} changed={filesChanged}
              onPicked={() => { if (window.matchMedia?.("(max-width: 1099px)").matches) setPane("chat"); }} />
          </section>
        )}
        {projectId && pane === "git" && (
          <section className="code-pane code-terminal code-active min-h-0 min-w-0 flex-1 flex-col">
            <GitPanel key={projectId} projectId={projectId} onOpenFile={openPath} />
          </section>
        )}
        <section
          className={`code-pane code-chat min-h-0 min-w-0 flex-1 flex-col ${pane === "chat" ? "code-active" : ""}`}
        >
          {projectId ? (
            <ChatPanel
              key={projectId}
              domain="code"
              projectId={projectId}
              onFileChange={agentChanged}
              onOpenFile={openPath}
            />
          ) : (
            <div className="grid flex-1 place-items-center overflow-y-auto px-6 py-8 text-center">
              {loadingProjects ? <p className="text-sm text-neutral-500">Загрузка проектов…</p> : <div className="max-w-lg">
                <span className="empty-orb"><Sparkles className="h-6 w-6" /></span>
                <h2 className="text-2xl font-semibold md:text-3xl">Всё начинается с идеи</h2>
                <p className="mx-auto mt-4 max-w-md text-sm leading-7 text-neutral-400">Создайте проект и расскажите Layla, что хотите сделать. Файлы, код и история работы будут в одном месте.</p>
                <div className="mt-7 flex flex-wrap justify-center gap-3">
                  <button className="primary-button" onClick={() => { setForm("local"); setPane("projects"); }}><Plus className="h-4 w-4" />Создать проект</button>
                  <button className="secondary-button" onClick={() => { setForm("import"); setPane("projects"); }}><GitBranch className="h-4 w-4" />Импортировать из Git</button>
                </div>
                <p className="mt-5 text-xs text-neutral-500">Можно работать без GitHub и скачать проект в ZIP</p>
              </div>}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
