import { create } from "zustand";
import { api, ApiError, type Me } from "@/lib/api";

interface AuthState {
  user: Me | null;
  loading: boolean;
  loaded: boolean;
  fetchMe: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

export const useAuth = create<AuthState>((set) => ({
  user: null,
  loading: false,
  loaded: false,
  fetchMe: async () => {
    set({ loading: true });
    try {
      const user = await api.get<Me>("/auth/me");
      set({ user, loaded: true });
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) set({ user: null, loaded: true });
    } finally {
      set({ loading: false });
    }
  },
  login: async (email, password) => {
    const user = await api.post<Me>("/auth/login", { email, password });
    set({ user, loaded: true });
  },
  logout: async () => {
    await api.post("/auth/logout");
    set({ user: null });
  },
}));
