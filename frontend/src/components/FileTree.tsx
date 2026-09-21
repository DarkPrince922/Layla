"use client";

import { useEffect, useState } from "react";
import { ChevronRight, ChevronDown, File as FileIcon, Folder } from "lucide-react";
import { api, type FileNode } from "@/lib/api";

function Node({
  projectId,
  node,
  depth,
  onOpen,
}: {
  projectId: string;
  node: FileNode;
  depth: number;
  onOpen: (path: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [children, setChildren] = useState<FileNode[] | null>(null);
  const [loading, setLoading] = useState(false);

  async function toggle() {
    if (!node.is_dir) {
      onOpen(node.path);
      return;
    }
    const next = !open;
    setOpen(next);
    if (next && children === null) {
      setLoading(true);
      try {
        const data = await api.get<FileNode[]>(
          `/projects/${projectId}/files?path=${encodeURIComponent(node.path)}`,
        );
        setChildren(data);
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <div>
      <button
        onClick={toggle}
        className="flex w-full items-center gap-1 rounded px-1 py-0.5 text-left text-xs text-neutral-300 hover:bg-ink-800"
        style={{ paddingLeft: depth * 12 + 4 }}
      >
        {node.is_dir ? (
          open ? (
            <ChevronDown className="h-3 w-3 shrink-0" />
          ) : (
            <ChevronRight className="h-3 w-3 shrink-0" />
          )
        ) : (
          <span className="w-3" />
        )}
        {node.is_dir ? (
          <Folder className="h-3.5 w-3.5 shrink-0 text-amber-400/80" />
        ) : (
          <FileIcon className="h-3.5 w-3.5 shrink-0 text-neutral-500" />
        )}
        <span className="truncate">{node.name}</span>
      </button>
      {open && (
        <div>
          {loading && <div className="px-2 py-0.5 text-[11px] text-neutral-600">загрузка…</div>}
          {children?.map((c) => (
            <Node key={c.path} projectId={projectId} node={c} depth={depth + 1} onOpen={onOpen} />
          ))}
        </div>
      )}
    </div>
  );
}

export function FileTree({
  projectId,
  onOpen,
}: {
  projectId: string;
  onOpen: (path: string) => void;
}) {
  const [roots, setRoots] = useState<FileNode[] | null>(null);

  useEffect(() => {
    let alive = true;
    api
      .get<FileNode[]>(`/projects/${projectId}/files?path=.`)
      .then((r) => alive && setRoots(r))
      .catch(() => alive && setRoots([]));
    return () => {
      alive = false;
    };
  }, [projectId]);

  if (roots === null) return <div className="p-2 text-xs text-neutral-600">загрузка…</div>;
  if (roots.length === 0) return <div className="p-2 text-xs text-neutral-600">пусто</div>;
  return (
    <div className="py-1">
      {roots.map((n) => (
        <Node key={n.path} projectId={projectId} node={n} depth={0} onOpen={onOpen} />
      ))}
    </div>
  );
}
