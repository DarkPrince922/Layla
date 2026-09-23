"use client";

import { useEffect } from "react";
import { create } from "zustand";

// Своё окно подтверждения вместо window.confirm: системный диалог бывает
// заблокирован (встроенные браузеры приложений, «запретить диалоги на этой
// странице») и тогда молча возвращает «нет» — удаление просто ничего не делало.
type Request = { message: string; confirmLabel: string; resolve: (ok: boolean) => void };

const useConfirm = create<{ request: Request | null; show: (request: Request | null) => void }>(set => ({
  request: null,
  show: request => set({ request }),
}));

export function confirmAction(message: string, confirmLabel = "Удалить"): Promise<boolean> {
  return new Promise(resolve => {
    useConfirm.getState().request?.resolve(false);
    useConfirm.getState().show({ message, confirmLabel, resolve });
  });
}

export function ConfirmHost() {
  const request = useConfirm(s => s.request);
  const show = useConfirm(s => s.show);
  const close = (ok: boolean) => { request?.resolve(ok); show(null); };
  useEffect(() => {
    if (!request) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") { request.resolve(false); show(null); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [request, show]);
  if (!request) return null;
  return (
    <div role="dialog" aria-modal="true" aria-label="Подтверждение" onClick={() => close(false)}
      className="fixed inset-0 z-[100] grid place-items-center bg-black/60 p-4">
      <div onClick={e => e.stopPropagation()}
        className="w-full max-w-sm rounded-2xl border border-ink-600 bg-ink-900 p-5 shadow-floating">
        <p className="whitespace-pre-line text-sm leading-6 text-neutral-200">{request.message}</p>
        <div className="mt-5 flex justify-end gap-2">
          <button className="secondary-button text-xs" onClick={() => close(false)}>Отмена</button>
          <button autoFocus onClick={() => close(true)}
            className="rounded-lg bg-red-600 px-3 py-2 text-xs font-medium text-white hover:bg-red-500">
            {request.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
