import { createContext, ReactNode, useCallback, useContext } from 'react';
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom';

/** 当前所在的顶层 URI 段(basename,如 ``/manager``)。空串=根。 */
const BasenameContext = createContext('');

/**
 * 在某个顶层 URI 段(basename)内提供"子路径相对路由"。
 * 底层基于 react-router 的 history;**保留旧 ``useRouter`` API**(path/params/navigate)
 * 以零改动复用既有管理面页面——它们仍用 ``/instances`` 等子路径,basename 自动补 ``/manager``。
 */
export function RouterProvider({ children, basename = '' }: { children: ReactNode; basename?: string }) {
  return <BasenameContext.Provider value={basename}>{children}</BasenameContext.Provider>;
}

interface RouterValue {
  /** 去掉 basename 后的子路径(如 ``/instances``;在 basename 根时为 ``/``)。 */
  path: string;
  params: URLSearchParams;
  /** 接收子路径(如 ``/instances``),自动补上 basename 后跳转。 */
  navigate: (path: string) => void;
}

export function useRouter(): RouterValue {
  const basename = useContext(BasenameContext);
  const location = useLocation();
  const navigateRR = useNavigate();
  const [searchParams] = useSearchParams();

  let path = location.pathname;
  if (basename && (path === basename || path.startsWith(`${basename}/`))) {
    path = path.slice(basename.length) || '/';
  }

  const navigate = useCallback(
    (target: string) => {
      const sub = target.startsWith('/') ? target : `/${target}`;
      navigateRR(`${basename}${sub}`);
    },
    [basename, navigateRR],
  );

  return { path, params: searchParams, navigate };
}

/** 匹配 ``/a/:foo/b`` 风格的路径，返回 params 字典或 null。(纯工具) */
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
