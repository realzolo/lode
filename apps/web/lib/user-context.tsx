'use client';

// Single source of truth for the authenticated user.
//
// The role used to be persisted in localStorage and re-hydrated by hand, which
// is a fragile client-state patch: a stale/cloned token or a role change on the
// server would silently disagree with the UI. Instead we keep the user derived
// from the backend's `/auth/me` (the same authority that authorizes every API
// call) in React context, seeded once on mount and refreshed after mutations.

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from 'react';
import { clearToken, fetchCurrentUser, getToken, SESSION_EXPIRED_EVENT } from '@/lib/api';
import type { CurrentUser } from '@/lib/types';

interface UserContextValue {
  user: CurrentUser | null;
  isAdmin: boolean;
  /** True until the initial /auth/me lookup resolves — avoids a role flash. */
  loading: boolean;
  /** The session exists but the backend could not verify it. */
  sessionError: boolean;
  /** Adopt the current user (e.g. right after login) without a refetch. */
  setUser: (user: CurrentUser) => void;
  /** Drop the session: clears the token and resets user state. */
  clearUser: () => void;
  /** Re-fetch /auth/me (used after role-changing mutations). */
  refresh: () => Promise<void>;
}

const UserContext = createContext<UserContextValue | null>(null);

export function UserProvider({ children }: { children: ReactNode }) {
  const [user, setUserState] = useState<CurrentUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [sessionError, setSessionError] = useState(false);

  const setUser = useCallback((u: CurrentUser) => {
    setUserState(u);
    setSessionError(false);
  }, []);
  const clearUser = useCallback(() => {
    clearToken();
    setUserState(null);
    setSessionError(false);
    setLoading(false);
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    setSessionError(false);
    if (!getToken()) {
      setUserState(null);
      setLoading(false);
      return;
    }
    try {
      setUserState(await fetchCurrentUser());
      setSessionError(false);
    } catch {
      setUserState(null);
      // A 401 clears the cookie in request(); other failures are recoverable
      // service/session checks and must not trigger a login redirect loop.
      setSessionError(getToken() !== null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const onSessionExpired = () => {
      setUserState(null);
      setSessionError(false);
      setLoading(false);
    };
    window.addEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
    return () => window.removeEventListener(SESSION_EXPIRED_EVENT, onSessionExpired);
  }, []);

  useEffect(() => {
    let active = true;
    if (!getToken()) {
      setSessionError(false);
      setLoading(false);
      return;
    }
    fetchCurrentUser()
      .then((u) => {
        if (active) {
          setUserState(u);
          setSessionError(false);
        }
      })
      .catch(() => {
        if (active) {
          setUserState(null);
          setSessionError(getToken() !== null);
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const isAdmin = user?.is_system_admin === true;

  return (
    <UserContext.Provider value={{ user, isAdmin, loading, sessionError, setUser, clearUser, refresh }}>
      {children}
    </UserContext.Provider>
  );
}

export function useUser(): UserContextValue {
  const ctx = useContext(UserContext);
  if (!ctx) {
    throw new Error('useUser must be used within a UserProvider');
  }
  return ctx;
}
