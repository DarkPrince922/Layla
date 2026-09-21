"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";

// Спец. §4 / §7.5: если приложение открыто по обычному HTTP, предупреждаем на
// любом экране ввода секретов и блокируем ввод ключей до явного подтверждения.
export function HttpKeyBanner() {
  const [insecure, setInsecure] = useState(false);

  useEffect(() => {
    // window.isSecureContext = true для https:// и для localhost.
    if (typeof window !== "undefined" && !window.isSecureContext) {
      setInsecure(true);
    }
  }, []);

  if (!insecure) return null;

  return (
    <div className="flex items-start gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <p className="font-medium">Небезопасное соединение (обычный HTTP)</p>
        <p className="text-amber-200/80">
          Введённые здесь секреты передаются в открытом виде. Откройте Layla по
          HTTPS, прежде чем добавлять API-ключи или SSH-ключи. Ввод ключей
          заблокирован, пока вы не подтвердите, что понимаете риск.
        </p>
      </div>
    </div>
  );
}
