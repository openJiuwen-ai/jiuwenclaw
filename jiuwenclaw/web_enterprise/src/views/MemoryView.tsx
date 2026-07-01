import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { webClient } from '../services/webClient';
import { useExtSettingsStore } from '../stores/extSettingsStore';

interface MemoryFile {
  path: string;
  name: string;
  type: 'index' | 'profile' | 'daily' | 'fact' | string;
  size: number;
  mtime: number;
}
interface MemoryContent {
  path: string;
  text: string;
  totalLines: number;
  fromLine: number;
  toLine: number;
  truncated: boolean;
}

/**
 * 记忆面板（claw_manager 用户面「记忆」标签，view=memory）。
 * 只读：走 web_enterprise 的 webClient（WS-RPC memory.list / memory.get，需 session_id，来自 connection.ack）。
 * 复用 agent 工作区里的现有记忆文件（MEMORY.md 索引 + memory/*.md）—— 不写、不改、仅查看。
 */
export function MemoryView() {
  const { t, i18n } = useTranslation();
  const { userId, groupId, botId } = useExtSettingsStore();
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [files, setFiles] = useState<MemoryFile[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [content, setContent] = useState<MemoryContent | null>(null);
  const [search, setSearch] = useState('');
  const [error, setError] = useState('');
  const [loadingDoc, setLoadingDoc] = useState(false);

  // 连接 + 从 connection.ack 拿 session_id
  useEffect(() => {
    let alive = true;
    const off = webClient.on('connection.ack', ({ payload }: { payload?: { session_id?: string } }) => {
      if (alive && payload?.session_id) setSessionId(payload.session_id);
    });
    webClient.connect({ userId, groupId, botId }).catch((e) => {
      if (alive) setError((e as Error)?.message || t('memory.loadFailed'));
    });
    return () => { alive = false; off(); };
  }, [userId, groupId, botId, t]);

  const loadList = useCallback(async (sid: string) => {
    try {
      const res = await webClient.request<{ files?: MemoryFile[] }>('memory.list', { session_id: sid });
      setFiles(res.files ?? []);
      setError('');
    } catch (e) {
      setError((e as Error)?.message || t('memory.loadFailed'));
    }
  }, [t]);

  const openFile = useCallback(async (sid: string, path: string) => {
    setSelected(path);
    setLoadingDoc(true);
    try {
      const res = await webClient.request<MemoryContent>('memory.get', { path, session_id: sid });
      setContent(res);
      setError('');
    } catch (e) {
      setContent(null);
      setError((e as Error)?.message || t('memory.loadFailed'));
    } finally {
      setLoadingDoc(false);
    }
  }, [t]);

  useEffect(() => { if (sessionId) void loadList(sessionId); }, [sessionId, loadList]);

  const typeLabel = (type: string) => t(`memory.type.${type}`, { defaultValue: type });
  const typeColor = (type: string) =>
    type === 'index' ? '#2563eb'
      : type === 'profile' ? '#16a34a'
        : type === 'daily' ? '#9333ea'
          : '#6b7280';

  const fmtTime = (mtime: number) => {
    try {
      return new Date(mtime * 1000).toLocaleString(i18n.language === 'en' ? 'en-US' : 'zh-CN');
    } catch { return ''; }
  };
  const fmtSize = (n: number) => (n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`);

  const filtered = useMemo(() => files.filter((f) => {
    const q = search.trim().toLowerCase();
    return !q || f.name.toLowerCase().includes(q) || f.path.toLowerCase().includes(q);
  }), [files, search]);

  const card = { border: '1px solid #e5e7eb', borderRadius: 8, padding: 10, marginBottom: 8, cursor: 'pointer' } as const;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', padding: 16, gap: 12, overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600 }}>{t('memory.title')}</h2>
        <div style={{ display: 'flex', gap: 8 }}>
          <input style={{ padding: '6px 8px', border: '1px solid #ccc', borderRadius: 6, width: 200 }}
            placeholder={t('memory.search')} value={search} onChange={(e) => setSearch(e.target.value)} />
          <button className="px-3 py-1 rounded border" disabled={!sessionId} onClick={() => sessionId && void loadList(sessionId)}>{t('memory.refresh')}</button>
        </div>
      </div>

      {!sessionId && <div style={{ color: '#888' }}>{t('memory.connecting')}</div>}
      {error && <div style={{ color: '#c00', fontSize: 13 }}>{error}</div>}

      <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>
        {/* 左：记忆文件列表 */}
        <div style={{ width: 300, flexShrink: 0, overflow: 'auto', paddingRight: 4 }}>
          {sessionId && filtered.length === 0 && <div style={{ color: '#888', fontSize: 13 }}>{t('memory.empty')}</div>}
          {filtered.map((f) => (
            <div key={f.path} style={{ ...card, borderColor: selected === f.path ? '#2563eb' : '#e5e7eb', background: selected === f.path ? '#f0f6ff' : '#fff' }}
              onClick={() => sessionId && void openFile(sessionId, f.path)}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ fontWeight: 500, wordBreak: 'break-all' }}>{f.name}</span>
                <span style={{ fontSize: 11, color: typeColor(f.type), border: `1px solid ${typeColor(f.type)}`, borderRadius: 4, padding: '0 4px', flexShrink: 0 }}>{typeLabel(f.type)}</span>
              </div>
              <div style={{ fontSize: 11, color: '#999', marginTop: 2 }}>{fmtSize(f.size)} · {fmtTime(f.mtime)}</div>
            </div>
          ))}
        </div>

        {/* 右：只读内容 */}
        <div style={{ flex: 1, minWidth: 0, border: '1px solid #e5e7eb', borderRadius: 8, overflow: 'auto', background: '#fafafa' }}>
          {!selected && <div style={{ color: '#aaa', padding: 16 }}>{t('memory.selectHint')}</div>}
          {selected && loadingDoc && <div style={{ color: '#888', padding: 16 }}>{t('memory.loadingDoc')}</div>}
          {selected && !loadingDoc && content && (
            <div>
              <div style={{ padding: '8px 12px', borderBottom: '1px solid #e5e7eb', fontSize: 12, color: '#666', position: 'sticky', top: 0, background: '#f3f4f6', display: 'flex', justifyContent: 'space-between' }}>
                <span style={{ wordBreak: 'break-all' }}>{content.path}</span>
                <span style={{ flexShrink: 0, marginLeft: 8 }}>
                  {t('memory.lines', { count: content.totalLines })}{content.truncated ? ` · ${t('memory.truncated')}` : ''}
                </span>
              </div>
              <pre style={{ margin: 0, padding: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-word', fontSize: 13, lineHeight: 1.5, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace' }}>{content.text || t('memory.emptyFile')}</pre>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
