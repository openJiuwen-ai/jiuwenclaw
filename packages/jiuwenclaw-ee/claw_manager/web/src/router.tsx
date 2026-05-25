import { createContext, ReactNode, useCallback, useContext, useEffect, useState } from 'react';

interface RouterContextValue {
  path: string;
  params: URLSearchParams;
  navigate: (path: string) => void;
}

const RouterContext = createContext<RouterContextValue | null>(null);

function readHash(): { path: string; params: URLSearchParams } {
  const raw = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash;
  const sep = raw.indexOf('?');
  const pathPart = sep >= 0 ? raw.slice(0, sep) : raw;
  const queryPart = sep >= 0 ? raw.slice(sep + 1) : '';
  return {
    path: pathPart || '/overview',
    params: new URLSearchParams(queryPart),
  };
}

export function RouterProvider({ children }: { children: ReactNode }) {
  const [{ path, params }, setState] = useState(() => readHash());

  useEffect(() => {
    const handler = () => setState(readHash());
    window.addEventListener('hashchange', handler);
    return () => window.removeEventListener('hashchange', handler);
  }, []);

  const navigate = useCallback((target: string) => {
    const next = target.startsWith('/') ? target : `/${target}`;
    if (window.location.hash === `#${next}`) return;
    window.location.hash = next;
  }, []);

  // 首次进入若无 hash 则补全
  useEffect(() => {
    if (!window.location.hash) {
      window.location.hash = '/overview';
    }
  }, []);

  return <RouterContext.Provider value={{ path, params, navigate }}>{children}</RouterContext.Provider>;
}

export function useRouter() {
  const ctx = useContext(RouterContext);
  if (!ctx) throw new Error('useRouter must be used within RouterProvider');
  return ctx;
}

/** 匹配 /a/:foo/b 风格的路径，返回 params 字典或 null。 */
export function matchRoute(pattern: string, path: string): Record<string, string> | null {
  const ps = pattern.replace(/^\//, '').split('/');
  const xs = path.replace(/^\//, '').split('/');
  if (ps.length !== xs.length) return null;
  const out: Record<string, string> = {};
  for (let i = 0; i < ps.length; i++) {
    const p = ps[i];
    const v = xs[i];
    if (p.startsWith(':')) {
      out[p.slice(1)] = decodeURIComponent(v);
    } else if (p !== v) {
      return null;
    }
  }
  return out;
}
