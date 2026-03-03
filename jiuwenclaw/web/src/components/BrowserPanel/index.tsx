import { useCallback, useEffect, useMemo, useState } from 'react';

interface BrowserPathPayload {
  chrome_path?: unknown;
}

interface BrowserStartPayload {
  returncode?: unknown;
}

interface BrowserPanelProps {
  isConnected: boolean;
  request: <T = unknown>(method: string, params?: Record<string, unknown>) => Promise<T>;
}

function normalizeChromePath(payload: unknown): string {
  if (!payload || typeof payload !== 'object') return '';
  const data = payload as BrowserPathPayload;
  return typeof data.chrome_path === 'string' ? data.chrome_path : '';
}

function normalizeReturnCode(payload: unknown): number | null {
  if (!payload || typeof payload !== 'object') return null;
  const data = payload as BrowserStartPayload;
  const code = Number(data.returncode);
  return Number.isInteger(code) ? code : null;
}

export function BrowserPanel({ isConnected, request }: BrowserPanelProps) {
  const [chromePath, setChromePath] = useState('');
  const [initialPath, setInitialPath] = useState('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const hasChanges = useMemo(() => chromePath !== initialPath, [chromePath, initialPath]);
  const isPathValid = useMemo(() => chromePath.trim().length > 0, [chromePath]);

  const clearFeedback = () => {
    setError(null);
    setSuccess(null);
  };

  const loadPath = useCallback(async () => {
    setLoading(true);
    clearFeedback();
    try {
      const payload = await request<BrowserPathPayload>('path.get');
      const value = normalizeChromePath(payload);
      setChromePath(value);
      setInitialPath(value);
    } catch (loadError) {
      const message = loadError instanceof Error ? loadError.message : '加载 Chrome 路径失败';
      setError(message);
    } finally {
      setLoading(false);
    }
  }, [request]);

  useEffect(() => {
    void loadPath();
  }, [loadPath]);

  useEffect(() => {
    if (!success) return;
    const timer = window.setTimeout(() => {
      setSuccess(null);
    }, 2500);
    return () => {
      window.clearTimeout(timer);
    };
  }, [success]);

  const handleSave = async () => {
    if (saving || !hasChanges || !isPathValid || !isConnected) {
      return;
    }
    setSaving(true);
    clearFeedback();
    try {
      const nextPath = chromePath.trim();
      const payload = await request<BrowserPathPayload>('path.set', { chrome_path: nextPath });
      const savedPath = normalizeChromePath(payload) || nextPath;
      setChromePath(savedPath);
      setInitialPath(savedPath);
      setSuccess('Chrome 路径已保存');
    } catch (saveError) {
      const message = saveError instanceof Error ? saveError.message : '保存 Chrome 路径失败';
      setError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleStart = async () => {
    if (starting || !isConnected) {
      return;
    }
    setStarting(true);
    clearFeedback();
    try {
      const payload = await request<BrowserStartPayload>('browser.start');
      const returncode = normalizeReturnCode(payload);
      if (returncode === null) {
        setSuccess('浏览器服务启动请求已发送');
      } else if (returncode === 0) {
        setSuccess('浏览器服务启动成功');
      } else {
        setError(`浏览器服务启动失败，返回码 ${returncode}`);
      }
    } catch (startError) {
      const message = startError instanceof Error ? startError.message : '启动浏览器服务失败';
      setError(message);
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="flex-1 min-h-0">
      <div className="card w-full h-full flex flex-col">
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <h2 className="text-lg font-semibold">浏览器服务</h2>
            <p className="text-sm text-text-muted mt-1">
              配置浏览器路径，启动浏览器服务。
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={() => void loadPath()}
              disabled={saving || starting}
              className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {loading ? '刷新中...' : '刷新路径'}
            </button>
          </div>
        </div>

        {error ? (
          <div className="mb-4 rounded-md border border-[var(--border-danger)] bg-danger-subtle px-3 py-2 text-sm text-danger">
            {error}
          </div>
        ) : null}
        {success ? (
          <div className="mb-4 rounded-md border border-[var(--border-ok)] bg-ok-subtle px-3 py-2 text-sm text-ok">
            {success}
          </div>
        ) : null}

        <div className="rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm">
          <div className="px-4 py-3 border-b border-border bg-secondary/30">
            <span className="text-xs text-text-muted uppercase tracking-wider font-medium">Chrome 路径配置</span>
          </div>
          <div className="p-4 space-y-4">
            <label className="block space-y-1.5">
              <span className="text-xs uppercase tracking-wide text-text-muted">chrome_path</span>
              <input
                type="text"
                value={chromePath}
                onChange={(event) => {
                  setChromePath(event.target.value);
                  if (error) setError(null);
                }}
                placeholder="例如：/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
                className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] text-text outline-none focus:border-accent"
                disabled={loading || saving || starting}
              />
            </label>

            {!isPathValid ? (
              <div className="text-xs text-danger">Chrome 路径不能为空</div>
            ) : null}

            <div className="flex items-center gap-2">
              <button
                type="button"
                className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                onClick={() => {
                  setChromePath(initialPath);
                  clearFeedback();
                }}
                disabled={!hasChanges || saving || starting}
              >
                取消
              </button>
              <button
                type="button"
                className="btn primary !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                onClick={() => void handleSave()}
                disabled={!isConnected || !hasChanges || !isPathValid || saving || starting || loading}
              >
                {saving ? '保存中...' : '保存路径'}
              </button>
              <button
                type="button"
                className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                onClick={() => void handleStart()}
                disabled={!isConnected || starting || saving}
              >
                {starting ? '启动中...' : '启动浏览器服务'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
