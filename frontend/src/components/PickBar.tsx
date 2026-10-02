"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";
import { MousePointerClick, Send, X } from "lucide-react";
import { withPicker, type PickedElement } from "@/lib/pick";

type FrameProps = {
  html?: string;
  src?: string;
  picking: boolean;
  onPicked: (element: PickedElement) => void;
  onCancel: () => void;
  title?: string;
  sandbox?: string;
  className?: string;
};

/**
 * Превью с режимом выбора элемента. Для srcDoc скрипт выбора вставляется сюда же;
 * для внешнего адреса (превью приложения) его вставляет прокси превью.
 */
export const PickFrame = forwardRef<HTMLIFrameElement, FrameProps>(function PickFrame(
  { html, src, picking, onPicked, onCancel, title = "preview", sandbox = "allow-scripts", className = "h-full w-full border-0" }, outer,
) {
  const frame = useRef<HTMLIFrameElement>(null);
  useImperativeHandle(outer, () => frame.current as HTMLIFrameElement);
  const picked = useRef(onPicked);
  const cancelled = useRef(onCancel);
  picked.current = onPicked;
  cancelled.current = onCancel;

  function tell(on: boolean) {
    frame.current?.contentWindow?.postMessage({ type: "layla-pick", on }, "*");
  }
  useEffect(() => { tell(picking); }, [picking]);
  useEffect(() => {
    const listen = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return;
      const data = event.data as { type?: string } & Partial<PickedElement>;
      if (data?.type === "layla-picked" && typeof data.selector === "string") {
        picked.current({ selector: data.selector, name: String(data.name || ""), html: String(data.html || ""), text: String(data.text || "") });
      } else if (data?.type === "layla-pick-cancel") cancelled.current();
    };
    window.addEventListener("message", listen);
    return () => window.removeEventListener("message", listen);
  }, []);

  return (
    <iframe ref={frame} title={title} sandbox={sandbox} className={className}
      srcDoc={html !== undefined ? withPicker(html) : undefined} src={html === undefined ? src : undefined}
      onLoad={() => tell(picking)} />
  );
});

/** Панель под превью: подсказка при выборе и поле «Что изменить?» для выбранного элемента. */
export function PickBar({ picking, element, onSend, onCancel, busy }: {
  picking: boolean;
  element: PickedElement | null;
  onSend: (request: string) => void;
  onCancel: () => void;
  busy?: boolean;
}) {
  const [request, setRequest] = useState("");
  const input = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { if (element) { setRequest(""); input.current?.focus(); } }, [element]);
  if (!picking && !element) return null;
  const send = () => { if (request.trim() && !busy) onSend(request.trim()); };
  return (
    <div className="shrink-0 border-t border-accent-500/30 bg-ink-900/95 p-3 text-xs">
      {!element ? (
        <div className="flex items-center gap-2 text-neutral-300">
          <MousePointerClick className="h-4 w-4 shrink-0 text-accent-300" />
          <span className="flex-1">Нажмите на элемент в превью, который нужно изменить. Esc — отмена.</span>
          <button onClick={onCancel} className="icon-button !h-7 !w-7" aria-label="Отменить выбор"><X className="h-3.5 w-3.5" /></button>
        </div>
      ) : (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <code className="min-w-0 truncate rounded-md bg-accent-500/15 px-2 py-1 text-accent-100" title={element.selector}>{element.name}</code>
            {element.text && <span className="min-w-0 flex-1 truncate text-neutral-400">«{element.text}»</span>}
            <button onClick={onCancel} className="icon-button ml-auto !h-7 !w-7" aria-label="Отменить правку"><X className="h-3.5 w-3.5" /></button>
          </div>
          <div className="flex items-end gap-2">
            <textarea ref={input} rows={2} value={request} onChange={e => setRequest(e.target.value)} aria-label="Что изменить"
              onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } if (e.key === "Escape") onCancel(); }}
              placeholder="Что изменить? Например: сделай кнопку крупнее и зелёной"
              className="min-w-0 flex-1 resize-none rounded-xl bg-ink-950 px-3 py-2 outline-none" />
            <button onClick={send} disabled={!request.trim() || busy} className="primary-button h-9 !px-3 text-xs"><Send className="h-3.5 w-3.5" />Изменить</button>
          </div>
        </div>
      )}
    </div>
  );
}
