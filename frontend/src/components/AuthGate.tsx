"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/store/auth";

// Client-side gate: fetch /me on mount, redirect to /login if unauthenticated.
export function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { user, loaded, fetchMe } = useAuth();

  useEffect(() => {
    if (!loaded) fetchMe();
  }, [loaded, fetchMe]);

  useEffect(() => {
    if (loaded && !user) router.replace("/login");
  }, [loaded, user, router]);

  if (!loaded) {
    return (
      <div className="grid h-screen place-items-center text-sm text-neutral-500">
        Loading Layla…
      </div>
    );
  }
  if (!user) return null;
  return <>{children}</>;
}
