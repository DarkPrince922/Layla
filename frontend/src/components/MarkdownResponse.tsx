"use client";

import { Children, isValidElement, useEffect, useRef, useState, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Check, Copy } from "lucide-react";
import { useLocale } from "@/store/locale";

function textOf(children: ReactNode): string {
  return Children.toArray(children).map(child => isValidElement<{ children?: ReactNode }>(child)
    ? textOf(child.props.children) : typeof child === "string" || typeof child === "number" ? String(child) : "").join("");
}

function CopyButton({ value, label }: { value: string | (() => string); label: string }) {
  const [copied, setCopied] = useState(false);
  const [manual, setManual] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const locale = useLocale(s => s.locale);
  useEffect(() => () => clearTimeout(timer.current), []);
  const copy = async () => {
    setCopied(false);
    const text = typeof value === "function" ? value() : value;
    try {
      // Clipboard API requires HTTPS. Older HTTP installations use the fallback.
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard unavailable");
      await navigator.clipboard.writeText(text);
    } catch {
      const previous = document.activeElement as HTMLElement | null;
      const field = document.createElement("textarea");
      field.value = text;
      field.style.cssText = "position:fixed;left:-9999px;top:0";
      document.body.appendChild(field);
      field.select();
      let ok = false;
      try { ok = document.execCommand("copy"); } catch { /* Offer manual selection below. */ }
      field.remove();
      previous?.focus();
      if (!ok) { setManual(text); return; }
    }
    setManual(null);
    setCopied(true);
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setCopied(false), 2000);
  };
  return <div className="min-w-0">
    <button type="button" onClick={copy} aria-label={label} className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-neutral-400 hover:bg-ink-700 hover:text-neutral-100">
      {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      <span aria-live="polite">{copied ? (locale === "ru" ? "Скопировано" : "Copied") : label}</span>
    </button>
    {manual !== null && <div className="mt-2 text-xs text-neutral-300" role="status">
      <p>{locale === "ru" ? "Не удалось скопировать автоматически. Выделите текст и скопируйте вручную:" : "Automatic copy failed. Select and copy the text below:"}</p>
      <textarea readOnly aria-label={locale === "ru" ? "Текст для копирования" : "Text to copy"} value={manual} onFocus={e => e.currentTarget.select()} className="mt-1 block max-h-48 w-full rounded bg-ink-950 p-2 font-mono" />
    </div>}
  </div>;
}

function CodeBlock({ children }: { children?: ReactNode }) {
  const locale = useLocale(s => s.locale);
  const code = Children.toArray(children).find(child => isValidElement(child));
  const language = isValidElement<{ className?: string }>(code)
    ? /language-([^\s]+)/.exec(code.props.className || "")?.[1] : undefined;
  const text = textOf(children).replace(/\n$/, "");
  return <div className="response-code my-3 min-w-0 overflow-hidden rounded-xl border border-ink-700 bg-ink-950/70">
    <div className="flex flex-wrap items-start justify-between gap-2 border-b border-ink-700 px-3 py-1.5">
      <span className="py-1 font-mono text-xs text-neutral-500">{language || "text"}</span>
      <CopyButton value={text} label={locale === "ru" ? "Копировать блок" : "Copy block"} />
    </div>
    <pre tabIndex={0} className="overflow-x-auto p-3 text-xs leading-6"><code>{text}</code></pre>
  </div>;
}

function Table({ children }: { children?: ReactNode }) {
  const table = useRef<HTMLTableElement>(null);
  const locale = useLocale(s => s.locale);
  const tsv = () => Array.from(table.current?.rows || []).map(row => Array.from(row.cells)
    .map(cell => (cell.textContent || "").replace(/[\t\r\n]+/g, " ")).join("\t")).join("\n");
  return <div className="my-3 min-w-0">
    <div className="mb-1 flex justify-end"><CopyButton value={tsv} label={locale === "ru" ? "Копировать таблицу" : "Copy table"} /></div>
    <div tabIndex={0} role="region" aria-label={locale === "ru" ? "Таблица ответа" : "Response table"} className="overflow-x-auto rounded-xl border border-ink-700">
      <table ref={table}>{children}</table>
    </div>
  </div>;
}

const components: Components = {
  pre: CodeBlock,
  table: Table,
  a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
  // Do not fetch arbitrary remote images merely because model output mentions them.
  img: ({ src, alt }) => <a href={src} target="_blank" rel="noopener noreferrer">{alt || src}</a>,
};

export function MarkdownResponse({ content, compactHeadings = false }: { content: string; compactHeadings?: boolean }) {
  const locale = useLocale(s => s.locale);
  return <div className="min-w-0 max-w-full">
    <div className={`agent-markdown ${compactHeadings ? "pentest-prose" : ""}`}><ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>{content}</ReactMarkdown></div>
    {!!content && <div className="mt-2"><CopyButton value={content} label={locale === "ru" ? "Копировать ответ" : "Copy response"} /></div>}
  </div>;
}
