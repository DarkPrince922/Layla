"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  ChevronRight,
  ChevronDown,
  File as FileIcon,
  Folder,
} from "lucide-react";
import { api, type FileNode } from "@/lib/api";

function Directory({
  projectId,
  path,
  depth,
  onOpen,
}: {
  projectId: string;
  path: string;
  depth: number;
  onOpen: (path: string) => void;
}) {
  const { data, error, isPending } = useQuery({
    queryKey: ["project-files", projectId, path],
    queryFn: () =>
      api.get<FileNode[]>(
        `/projects/${projectId}/files?path=${encodeURIComponent(path)}`,
      ),
  });
  if (isPending)
    return <p className="p-2 text-xs text-neutral-500">Загрузка…</p>;
  if (error)
    return (
      <p role="alert" className="p-2 text-xs text-red-400">
        {error.message}
      </p>
    );
  if (!data?.length)
    return <p className="p-2 text-xs text-neutral-500">Папка пуста</p>;
  return (
    <>
      {data.map((node) => (
        <Node key={node.path} {...{ projectId, node, depth, onOpen }} />
      ))}
    </>
  );
}

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
  return (
    <div>
      <button
        onClick={() => (node.is_dir ? setOpen(!open) : onOpen(node.path))}
        aria-expanded={node.is_dir ? open : undefined}
        className="flex w-full items-center gap-1 rounded py-1.5 pr-2 text-left text-xs text-neutral-300 hover:bg-ink-800"
        style={{ paddingLeft: depth * 12 + 8 }}
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
        <Directory
          projectId={projectId}
          path={node.path}
          depth={depth + 1}
          onOpen={onOpen}
        />
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
  return (
    <div className="py-1">
      <Directory projectId={projectId} path="." depth={0} onOpen={onOpen} />
    </div>
  );
}
