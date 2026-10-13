/** Authentication state. */

import { create } from "zustand";
import { authApi, REFRESH_KEY, TOKEN_KEY } from "@/api";
import { describeError } from "@/lib/errors";
import type { Role, User } from "@/types";

interface AuthState {
  user: User | null;
  loading: boolean;
  error: string | null;
  /** True once `loadUser()` has settled at least once. */
  initialised: boolean;
  isAuthenticated: () => boolean;
  hasRole: (minimum: Role) => boolean;
  login: (email: string, password: string) => Promise<boolean>;
  logout: () => void;
  loadUser: () => Promise<void>;
}

const ROLE_RANK: Record<Role, number> = {
  VIEWER: 0,
  AGENT: 1,
  OPERATOR: 2,
  ADMIN: 3,
};

export const useAuth = create<AuthState>((set, get) => ({
  user: null,
  loading: false,
  error: null,
  initialised: false,

  isAuthenticated: () => Boolean(get().user) || Boolean(localStorage.getItem(TOKEN_KEY)),

  hasRole: (minimum: Role) => {
    const user = get().user;
    if (!user) return false;
    return ROLE_RANK[user.role] >= ROLE_RANK[minimum];
  },

  async login(email: string, password: string) {
    set({ loading: true, error: null });
    try {
      await authApi.login(email, password);
      const user = await authApi.me();
      set({ user, loading: false, initialised: true, error: null });
      return true;
    } catch (err) {
      set({ loading: false, initialised: true, error: describeError(err).message, user: null });
      return false;
    }
  },

  logout() {
    authApi.logout();
    set({ user: null, error: null, initialised: true });
  },

  async loadUser() {
    const token = localStorage.getItem(TOKEN_KEY);
    if (!token) {
      set({ user: null, initialised: true, loading: false });
      return;
    }
    set({ loading: true });
    try {
      const user = await authApi.me();
      set({ user, loading: false, initialised: true, error: null });
    } catch {
      // Token is stale or revoked; drop it and fall back to the login screen.
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(REFRESH_KEY);
      set({ user: null, loading: false, initialised: true });
    }
  },
}));
