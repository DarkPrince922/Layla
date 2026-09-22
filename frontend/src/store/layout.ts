import { create } from "zustand";

export type LayoutMode = "desktop" | "mobile";

interface LayoutState {
  mode: LayoutMode;
  setMode: (m: LayoutMode) => void;
  apply: () => void;
}

function initial(): LayoutMode {
  if (typeof window === "undefined") return "desktop";
  try {
    return window.localStorage.getItem("layla.layout") === "mobile" ? "mobile" : "desktop";
  } catch {
    return "desktop";
  }
}

function setAttr(mode: LayoutMode) {
  if (typeof document !== "undefined") document.documentElement.dataset.layout = mode;
}

export const useLayout = create<LayoutState>((set, get) => ({
  mode: initial(),
  setMode: (m) => {
    try {
      window.localStorage.setItem("layla.layout", m);
    } catch {
      /* ignore */
    }
    setAttr(m);
    set({ mode: m });
  },
  apply: () => setAttr(get().mode),
}));
