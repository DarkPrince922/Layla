import { create } from "zustand";
import { type Locale, translate } from "@/lib/i18n";

interface LocaleState {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: string) => string;
}

function initial(): Locale {
  if (typeof window === "undefined") return "ru";
  try {
    const v = window.localStorage.getItem("layla.locale");
    return v === "en" || v === "ru" ? v : "ru";
  } catch {
    return "ru";
  }
}

export const useLocale = create<LocaleState>((set, get) => ({
  locale: initial(),
  setLocale: (l) => {
    try {
      window.localStorage.setItem("layla.locale", l);
    } catch {
      /* ignore */
    }
    set({ locale: l });
  },
  t: (key) => translate(get().locale, key),
}));
