import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';
import { AuthApi, AuthUser, clearTokens, hasSession, setUnauthorizedHandler } from '../services/api';

interface AuthContextValue {
  user: AuthUser | null;
  ready: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);

  // 启动时若已有 token，则校验 /me 恢复登录态。
  useEffect(() => {
    let alive = true;
    (async () => {
      if (hasSession()) {
        try {
          const me = await AuthApi.me();
          if (alive) setUser(me);
        } catch {
          clearTokens();
        }
      }
      if (alive) setReady(true);
    })();
    return () => {
      alive = false;
    };
  }, []);

  // 会话失效（401，且 refresh 续期也失败）→ 清登录态回到登录页。
  useEffect(() => {
    setUnauthorizedHandler(() => {
      clearTokens();
      setUser(null);
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    // AuthApi.login 内部完成 OAuth2 取 token + 存储,返回 /me 用户。
    const me = await AuthApi.login(username, password);
    setUser(me);
  }, []);

  const logout = useCallback(async () => {
    await AuthApi.logout(); // 内部已 try/catch + clearTokens
    setUser(null);
  }, []);

  return <AuthContext.Provider value={{ user, ready, login, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error('useAuth must be used within AuthProvider');
  return ctx;
}
