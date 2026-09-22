"use client";
import { useEffect, useState, type ReactNode } from "react";
import { ChatPanel } from "@/components/ChatPanel";
export function DomainChatWorkspace({ domain, children }: { domain: string; children: ReactNode }) {
  const [chat, setChat] = useState(true);
  useEffect(() => {
    if (new URLSearchParams(window.location.search).get("view") === "workspace") setChat(false);
    const workspace = (e: Event) => { if ((e as CustomEvent).detail.domain === domain) setChat(false); };
    window.addEventListener("layla:open-workspace", workspace);
    const open = (e: Event) => { if ((e as CustomEvent).detail.domain === domain) setChat(true); };
    window.addEventListener("layla:open-chat", open);
    return () => { window.removeEventListener("layla:open-chat", open); window.removeEventListener("layla:open-workspace", workspace); };
  }, [domain]);
  return <div className="flex h-full min-h-0 flex-col"><div className="workspace-toolbar"><div className="workspace-title"><h1>{domain === "design" ? "Дизайн" : "Пентест"}</h1><p>Ваши задачи, файлы и история</p></div><div className="segmented-control"><button className="segment" aria-pressed={chat} onClick={() => setChat(true)}>Чаты</button><button className="segment" aria-pressed={!chat} onClick={() => setChat(false)}>{domain === "design" ? "Бриф и превью" : "Проекты и инструменты"}</button></div></div><div className="min-h-0 flex-1">{chat ? <ChatPanel domain={domain} /> : children}</div></div>;
}
