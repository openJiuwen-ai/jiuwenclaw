import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { LanguageSwitcher } from '../../components/LanguageSwitcher';
import { ThemeToggle } from '../../components/ThemeToggle';
import { useAuth } from '../../auth/AuthContext';
import { useAsync } from '../../hooks/useAsync';
import { Bot, MeApi, Org } from '../../services/api';

// 内嵌聊天(web_enterprise)的基址：默认同源 /chat（webui nginx 以 base=/chat/ 同源提供 dist，
// 不再指向 :5173，因而不会"localhost 拒绝访问"；其 WS/file-api 走根路径,由 webui 反代到 web 后端)。
const CHAT_BASE = (import.meta.env.VITE_CHAT_BASE_URL as string | undefined) || '/chat';

export function UserConsole() {
  const { t } = useTranslation();
  const { user, logout } = useAuth();
  const { data: orgsData } = useAsync(() => MeApi.orgs(), []);
  const [orgId, setOrgId] = useState<string | null>(null);
  const orgs = orgsData?.orgs ?? [];
  const currentOrg = orgs.find((o) => o.group_id === orgId) ?? null;

  return (
    // 自带高度链：100vh 的纵向 flex，topbar 固定高、body 占满剩余(min-height:0 关键)
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', overflow: 'hidden' }}>
      <header className="topbar" style={{ flexShrink: 0 }}>
        <div className="brand">
          <img src="/logo.png" alt="JiuwenClaw" className="brand-logo-img" />
          <div className="brand-text">
            <span className="brand-title">JiuwenClaw</span>
            <span className="brand-sub">{t('userConsole.brandSub')}</span>
          </div>
        </div>
        <div className="flex items-center gap-3">
          <LanguageSwitcher />
          <ThemeToggle />
          <span className="text-sm text-muted">{user?.display_name}</span>
          <button className="btn" onClick={() => void logout()}>{t('auth.logout')}</button>
        </div>
      </header>

      <div style={{ flex: 1, minHeight: 0, display: 'flex' }}>
        {!currentOrg ? (
          <OrgPicker orgs={orgs} onPick={setOrgId} />
        ) : (
          <OrgWorkspace org={currentOrg} userId={user?.user_id ?? ''} onSwitchOrg={() => setOrgId(null)} />
        )}
      </div>
    </div>
  );
}

function OrgPicker({ orgs, onPick }: { orgs: Org[]; onPick: (gid: string) => void }) {
  const { t } = useTranslation();
  return (
    <div style={{ flex: 1, overflow: 'auto', padding: '20px 24px' }}>
      <h2 className="card-title mb-1">{t('userConsole.myOrgs')}</h2>
      <p className="text-sm text-muted mb-3">{t('userConsole.pickOrgFirst')}</p>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12 }}>
        {orgs.map((o) => (
          <button key={o.group_id} className="card" style={{ width: 200, textAlign: 'left', cursor: 'pointer' }} onClick={() => onPick(o.group_id)}>
            <div className="card-title">{o.name}</div>
            <div className="text-xs text-muted mono">{o.group_id}</div>
          </button>
        ))}
        {orgs.length === 0 && <div className="text-muted">{t('userConsole.noOrgs')}</div>}
      </div>
    </div>
  );
}

function OrgWorkspace({ org, userId, onSwitchOrg }: { org: Org; userId: string; onSwitchOrg: () => void }) {
  const { t } = useTranslation();
  const { data: botsData, loading } = useAsync(() => MeApi.bots(org.group_id), [org.group_id]);
  const [botId, setBotId] = useState<string | null>(null);
  const bots = botsData?.bots ?? [];
  const currentBot = bots.find((b) => b.bot_id === botId) ?? null;

  // 切组织后重置选中的 bot
  useEffect(() => { setBotId(null); }, [org.group_id]);

  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
      {/* 左：组织信息 + bot 列表 */}
      <aside style={{ width: 240, borderRight: '1px solid var(--border, #e5e7eb)', padding: 12, overflowY: 'auto', flexShrink: 0 }}>
        <div className="flex items-center justify-between mb-2">
          <div className="card-title" style={{ fontSize: 14 }}>{org.name}</div>
          <button className="btn ghost sm" onClick={onSwitchOrg}>{t('userConsole.switchOrg')}</button>
        </div>
        <div className="nav-group-title nav-group-title--uppercase">{t('userConsole.availableBots')}</div>
        <div className="space-y-1">
          {bots.map((b) => (
            <button key={b.bot_id} className={`nav-item ${botId === b.bot_id ? 'active' : ''}`} onClick={() => setBotId(b.bot_id)}>
              {b.name}
            </button>
          ))}
          {!loading && bots.length === 0 && <div className="text-xs text-muted" style={{ padding: 8 }}>{t('userConsole.noBots')}</div>}
        </div>
      </aside>

      {/* 右：选中 bot 的工作区 */}
      <section style={{ flex: 1, minWidth: 0, minHeight: 0, display: 'flex' }}>
        {currentBot ? (
          <BotWorkspace bot={currentBot} userId={userId} groupId={org.group_id} />
        ) : (
          <div className="flex items-center justify-center" style={{ flex: 1 }}>
            <div className="text-muted">{t('userConsole.pickBot')}</div>
          </div>
        )}
      </section>
    </div>
  );
}

type TabKey = 'chat' | 'schedule' | 'skills' | 'memory';

/** 聊天 iframe 加载中的遮罩：大旋转图标 + 说明；加载偏慢再补一行提示。 */
function ChatLoading() {
  const { t } = useTranslation();
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    const id = window.setTimeout(() => setSlow(true), 12000);
    return () => window.clearTimeout(id);
  }, []);
  return (
    <div
      style={{
        position: 'absolute', inset: 0, display: 'flex', flexDirection: 'column',
        alignItems: 'center', justifyContent: 'center', gap: 14,
        background: 'var(--bg-content, var(--bg, #fff))',
      }}
    >
      <svg width="64" height="64" viewBox="0 0 50 50" aria-hidden="true">
        <circle cx="25" cy="25" r="20" fill="none" stroke="var(--border, #e5e7eb)" strokeWidth="4" />
        <path d="M25 5 a20 20 0 0 1 20 20" fill="none" stroke="var(--accent, #6366f1)" strokeWidth="4" strokeLinecap="round">
          <animateTransform attributeName="transform" type="rotate" from="0 25 25" to="360 25 25" dur="0.9s" repeatCount="indefinite" />
        </path>
      </svg>
      <div className="text-sm text-muted">{t('userConsole.chatLoading')}</div>
      {slow && <div className="text-xs text-muted" style={{ maxWidth: 320, textAlign: 'center' }}>{t('userConsole.chatLoadingSlow')}</div>}
    </div>
  );
}

// 标签 → web_enterprise 视图(空串=默认聊天)
const IFRAME_VIEW: Partial<Record<TabKey, string>> = { chat: '', schedule: 'schedule', skills: 'skills', memory: 'memory' };

function BotWorkspace({ bot, userId, groupId }: { bot: Bot; userId: string; groupId: string }) {
  const { t, i18n } = useTranslation();
  // 内嵌 iframe(web_enterprise)与父窗口不同源,语言状态独立 → 用 postMessage 同步语言
  const iframeRefs = useRef<Map<string, HTMLIFrameElement>>(new Map());
  const postLang = useCallback(
    (el: HTMLIFrameElement | null) =>
      el?.contentWindow?.postMessage({ type: 'jw-set-lang', lang: i18n.language.startsWith('en') ? 'en' : 'zh' }, '*'),
    [i18n.language],
  );
  // 语言切换时,通知所有已加载的内嵌视图
  useEffect(() => {
    iframeRefs.current.forEach((el) => postLang(el));
  }, [postLang]);

  const tabs: { key: TabKey; label: string }[] = [
    { key: 'chat', label: t('userConsole.tabChat') },
    { key: 'schedule', label: t('userConsole.tabSchedule') },
    { key: 'skills', label: t('userConsole.tabSkills') },
    { key: 'memory', label: t('userConsole.tabMemory') },
  ];

  // (user_id, group_id, bot_id) 经 query 注入 web_enterprise → extSettings 读取 → WS 透传 → 铆钉 agent
  const baseQuery = useMemo(
    () => new URLSearchParams({ user_id: userId, group_id: groupId, bot_id: bot.bot_id }).toString(),
    [userId, groupId, bot.bot_id],
  );
  const urlFor = (view: string) => (view ? `${CHAT_BASE}/?${baseQuery}&view=${view}` : `${CHAT_BASE}/?${baseQuery}`);

  const [tab, setTab] = useState<TabKey>('chat');
  const [visited, setVisited] = useState<Set<TabKey>>(() => new Set<TabKey>(['chat']));
  const [loadedViews, setLoadedViews] = useState<Set<string>>(new Set());

  // 切 bot/组织（baseQuery 变 → 所有 iframe key 变 → 重挂）→ 复位到聊天标签与加载态
  useEffect(() => {
    setTab('chat');
    setVisited(new Set<TabKey>(['chat']));
    setLoadedViews(new Set());
  }, [baseQuery]);

  // 首次切到某个 iframe 标签 → 懒挂载（此后常驻，不再卸载）
  useEffect(() => {
    if (IFRAME_VIEW[tab] !== undefined) {
      setVisited((p) => (p.has(tab) ? p : new Set(p).add(tab)));
    }
  }, [tab]);

  const activeView = IFRAME_VIEW[tab];
  const activeIsIframe = activeView !== undefined;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', flex: 1, minHeight: 0 }}>
      <div style={{ display: 'flex', gap: 4, padding: '8px 12px', borderBottom: '1px solid var(--border, #e5e7eb)', flexShrink: 0 }}>
        {tabs.map((tb) => (
          <button key={tb.key} className={`btn sm ${tab === tb.key ? 'primary' : 'ghost'}`} onClick={() => setTab(tb.key)}>
            {tb.label}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <span className="text-xs text-muted" style={{ alignSelf: 'center' }}>bot: <span className="mono">{bot.bot_id}</span></span>
      </div>
      <div style={{ flex: 1, minHeight: 0, position: 'relative' }}>
        {/* iframe 标签常驻挂载（懒加载后不卸载）：切标签只隐藏、不重载不断连；切 bot → key 变 → 重挂重连 */}
        {(['chat', 'schedule', 'skills', 'memory'] as TabKey[]).filter((k) => visited.has(k)).map((k) => {
          const view = IFRAME_VIEW[k] ?? '';
          return (
            <iframe
              key={`${baseQuery}|${view}`}
              ref={(el) => { if (el) iframeRefs.current.set(view, el); else iframeRefs.current.delete(view); }}
              src={urlFor(view)}
              title={k}
              onLoad={(e) => { setLoadedViews((p) => new Set(p).add(view)); postLang(e.currentTarget); }}
              style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', border: 0, display: tab === k ? 'block' : 'none' }}
            />
          );
        })}
        {activeIsIframe && !loadedViews.has(activeView as string) && <ChatLoading />}
        {!activeIsIframe && (
          <div className="flex items-center justify-center" style={{ position: 'absolute', inset: 0 }}>
            <div className="text-muted">{t('userConsole.tabWip')}</div>
          </div>
        )}
      </div>
    </div>
  );
}
