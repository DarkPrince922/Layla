"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useAppearance } from "@/store/appearance";

export function Providers({ children }: { children: ReactNode }) {
  const [client] = useState(() => new QueryClient());
  const applyAppearance = useAppearance(s => s.apply);
  useEffect(() => { applyAppearance(); }, [applyAppearance]);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
