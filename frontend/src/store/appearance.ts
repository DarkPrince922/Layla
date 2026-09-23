import { create } from "zustand";

function applyMotion(motion: boolean) {
  if (typeof document !== "undefined") document.documentElement.dataset.motion = motion ? "full" : "reduced";
}

export const useAppearance = create<{
  motion: boolean;
  setMotion: (motion: boolean) => void;
  apply: () => void;
}>((set) => ({
  motion: true,
  setMotion: (motion) => {
    try { window.localStorage.setItem("layla.motion", String(motion)); } catch { /* Storage can be unavailable. */ }
    applyMotion(motion);
    set({ motion });
  },
  apply: () => {
    let motion = true;
    try { motion = window.localStorage.getItem("layla.motion") !== "false"; } catch { /* Use the default. */ }
    applyMotion(motion);
    set({ motion });
  },
}));
