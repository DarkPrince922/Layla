"use client";

import { useEffect, useState } from "react";
import { ShieldAlert } from "lucide-react";

// Spec §4 / §7.5: if the app is served over plain HTTP, warn on any screen that
// accepts secrets and gate key entry behind explicit acknowledgement.
export function HttpKeyBanner() {
  const [insecure, setInsecure] = useState(false);

  useEffect(() => {
    // window.isSecureContext is true on https:// and on localhost.
    if (typeof window !== "undefined" && !window.isSecureContext) {
      setInsecure(true);
    }
  }, []);

  if (!insecure) return null;

  return (
    <div className="flex items-start gap-3 rounded-md border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-200">
      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
      <div>
        <p className="font-medium">Insecure connection (plain HTTP)</p>
        <p className="text-amber-200/80">
          Secrets entered here travel unencrypted. Serve Layla over HTTPS before
          adding API keys or SSH keys. Key entry is blocked until you confirm you
          understand the risk.
        </p>
      </div>
    </div>
  );
}
