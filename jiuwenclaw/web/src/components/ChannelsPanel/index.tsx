import { useCallback, useEffect, useMemo, useState } from 'react';
import { webRequest } from '../../services/webClient';
import './ChannelsPanel.css';

interface ChannelsPanelProps {
  isConnected: boolean;
}

type ChannelItem = {
  channel_id: SupportedChannelId;
  label: string;
  logo_src: string | null;
  enabled: boolean;
};

type LoadState = 'idle' | 'loading' | 'success' | 'error';
type SupportedChannelId = 'web' | 'xiaoyi' | 'feishu';

type FeishuConfig = {
  enabled: boolean;
  app_id: string;
  app_secret: string;
  encrypt_key: string;
  verification_token: string;
  allow_from: string[];
};

type FeishuDraft = {
  enabled: boolean;
  app_id: string;
  app_secret: string;
  encrypt_key: string;
  verification_token: string;
  allow_from: string;
};

type XiaoyiConfig = {
  enabled: boolean;
  ak: string;
  sk: string;
  agent_id: string;
  ws_url1: string;
  ws_url2: string;
  enable_streaming: boolean;
};

type XiaoyiDraft = {
  enabled: boolean;
  ak: string;
  sk: string;
  agent_id: string;
  ws_url1: string;
  ws_url2: string;
  enable_streaming: boolean;
};

const DEFAULT_FEISHU_CONF: FeishuConfig = {
  enabled: false,
  app_id: '',
  app_secret: '',
  encrypt_key: '',
  verification_token: '',
  allow_from: [],
};

const DEFAULT_XIAOYI_CONF: XiaoyiConfig = {
  enabled: false,
  ak: '',
  sk: '',
  agent_id: '',
  ws_url1: '',
  ws_url2: '',
  enable_streaming: true,
};

const SUPPORTED_CHANNELS: Array<{ channel_id: SupportedChannelId; label: string; logo_src: string | null }> = [
  { channel_id: 'web', label: '网页', logo_src: null },
  { channel_id: 'xiaoyi', label: '小艺', logo_src: '/xiaoyi.webp' },
  { channel_id: 'feishu', label: '飞书', logo_src: '/feishu.webp' },
];


function formatTime(iso: string | null): string {
  if (!iso) return '-';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '-';
  return date.toLocaleTimeString('zh-CN', { hour12: false });
}

function isSensitiveField(field: keyof FeishuDraft): boolean {
  return field === 'app_secret' || field === 'encrypt_key' || field === 'verification_token';
}

function isSensitiveXiaoyiField(field: keyof XiaoyiDraft): boolean {
  return field === 'ak' || field === 'sk';
}

function isReadonlyXiaoyiField(field: keyof XiaoyiDraft): boolean {
  return field === 'ws_url1' || field === 'ws_url2';
}

function normalizeEnabledChannels(channels: unknown): Set<string> {
  if (!Array.isArray(channels)) {
    return new Set();
  }
  return new Set(
    channels
    .map((item) => {
      if (!item || typeof item !== 'object') {
        return null;
      }
      const channelId = (item as { channel_id?: unknown }).channel_id;
      if (typeof channelId !== 'string' || !channelId.trim()) {
        return null;
      }
      return channelId.trim().toLowerCase();
    })
      .filter((item): item is string => item !== null),
  );
}

function buildChannels(channels: unknown): ChannelItem[] {
  const enabledChannels = normalizeEnabledChannels(channels);
  return SUPPORTED_CHANNELS.map((channel) => ({
    ...channel,
    enabled: enabledChannels.has(channel.channel_id),
  }));
}

function normalizeFeishuConfig(input: unknown): FeishuConfig {
  if (!input || typeof input !== 'object') {
    return DEFAULT_FEISHU_CONF;
  }
  const data = input as Record<string, unknown>;
  const allowFromRaw = Array.isArray(data.allow_from) ? data.allow_from : [];
  const allowFrom = allowFromRaw
    .map((item) => String(item ?? '').trim())
    .filter((item) => item.length > 0);
  return {
    enabled: Boolean(data.enabled),
    app_id: String(data.app_id ?? '').trim(),
    app_secret: String(data.app_secret ?? '').trim(),
    encrypt_key: String(data.encrypt_key ?? '').trim(),
    verification_token: String(data.verification_token ?? '').trim(),
    allow_from: allowFrom,
  };
}

function draftFromFeishuConfig(conf: FeishuConfig): FeishuDraft {
  return {
    enabled: conf.enabled,
    app_id: conf.app_id,
    app_secret: conf.app_secret,
    encrypt_key: conf.encrypt_key,
    verification_token: conf.verification_token,
    allow_from: conf.allow_from.join('\n'),
  };
}

function normalizeAllowFromText(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter((item) => item.length > 0);
}

function buildFeishuPayload(draft: FeishuDraft): Record<string, unknown> {
  return {
    enabled: draft.enabled,
    app_id: draft.app_id.trim(),
    app_secret: draft.app_secret.trim(),
    encrypt_key: draft.encrypt_key.trim(),
    verification_token: draft.verification_token.trim(),
    allow_from: normalizeAllowFromText(draft.allow_from),
  };
}

function normalizeXiaoyiConfig(input: unknown): XiaoyiConfig {
  if (!input || typeof input !== 'object') {
    return DEFAULT_XIAOYI_CONF;
  }
  const data = input as Record<string, unknown>;
  return {
    enabled: Boolean(data.enabled),
    ak: String(data.ak ?? '').trim(),
    sk: String(data.sk ?? '').trim(),
    agent_id: String(data.agent_id ?? '').trim(),
    ws_url1: String(data.ws_url1 ?? '').trim(),
    ws_url2: String(data.ws_url2 ?? '').trim(),
    enable_streaming: data.enable_streaming === undefined ? true : Boolean(data.enable_streaming),
  };
}

function draftFromXiaoyiConfig(conf: XiaoyiConfig): XiaoyiDraft {
  return {
    enabled: conf.enabled,
    ak: conf.ak,
    sk: conf.sk,
    agent_id: conf.agent_id,
    ws_url1: conf.ws_url1,
    ws_url2: conf.ws_url2,
    enable_streaming: conf.enable_streaming,
  };
}

function buildXiaoyiPayload(draft: XiaoyiDraft): Record<string, unknown> {
  return {
    enabled: draft.enabled,
    ak: draft.ak.trim(),
    sk: draft.sk.trim(),
    agent_id: draft.agent_id.trim(),
    ws_url1: draft.ws_url1.trim(),
    ws_url2: draft.ws_url2.trim(),
    enable_streaming: draft.enable_streaming,
  };
}

function VisibilityIcon({ visible }: { visible: boolean }) {
  return visible ? (
    <svg className="channels-panel__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 3l18 18" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M10.58 10.58A2 2 0 0013.42 13.42" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.88 5.09A10.94 10.94 0 0112 4.9c5.05 0 9.27 3.11 10.5 7.5a11.6 11.6 0 01-3.06 4.88" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M6.61 6.61A11.6 11.6 0 001.5 12.4c.53 1.9 1.63 3.56 3.11 4.79" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M14.12 14.12a3 3 0 01-4.24-4.24" />
    </svg>
  ) : (
    <svg className="channels-panel__icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8}>
      <path strokeLinecap="round" strokeLinejoin="round" d="M1.5 12s3.75-7.5 10.5-7.5S22.5 12 22.5 12s-3.75 7.5-10.5 7.5S1.5 12 1.5 12z" />
      <circle cx="12" cy="12" r="3" />
    </svg>
  );
}

function ChannelLogo({ channel }: { channel: ChannelItem }) {
  if (channel.logo_src) {
    return (
      <img
        src={channel.logo_src}
        alt={`${channel.label} logo`}
        className="h-6 w-6 rounded-md border border-border object-contain bg-card"
      />
    );
  }
  return (
    <span className="h-6 w-6 rounded-md border border-border bg-card flex items-center justify-center text-text-muted">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} className="h-5 w-5">
        <circle cx="12" cy="12" r="9" />
        <path strokeLinecap="round" d="M3 12h18M12 3c2.5 2.2 4 5.5 4 9s-1.5 6.8-4 9m0-18c-2.5 2.2-4 5.5-4 9s1.5 6.8 4 9" />
      </svg>
    </span>
  );
}

function ChannelHeaderLogo({ channelId, label }: { channelId: SupportedChannelId; label: string }) {
  const logoSrc = SUPPORTED_CHANNELS.find((channel) => channel.channel_id === channelId)?.logo_src ?? null;
  if (logoSrc) {
    return (
      <img
        src={logoSrc}
        alt={`${label} logo`}
        className="h-9 w-9 rounded-lg border border-border object-contain bg-card"
      />
    );
  }
  return (
    <span className="h-9 w-9 rounded-lg border border-border bg-card flex items-center justify-center text-text-muted">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} className="h-7 w-7">
        <circle cx="12" cy="12" r="9" />
        <path strokeLinecap="round" d="M3 12h18M12 3c2.5 2.2 4 5.5 4 9s-1.5 6.8-4 9m0-18c-2.5 2.2-4 5.5-4 9s1.5 6.8 4 9" />
      </svg>
    </span>
  );
}

export function ChannelsPanel({ isConnected }: ChannelsPanelProps) {
  const [channels, setChannels] = useState<ChannelItem[]>(() => buildChannels([]));
  const [activeChannelId, setActiveChannelId] = useState<SupportedChannelId>('xiaoyi');
  const [loadState, setLoadState] = useState<LoadState>('idle');
  const [error, setError] = useState<string | null>(null);
  const [lastUpdatedAt, setLastUpdatedAt] = useState<string | null>(null);

  const [feishuConfig, setFeishuConfig] = useState<FeishuConfig>(DEFAULT_FEISHU_CONF);
  const [draft, setDraft] = useState<FeishuDraft>(draftFromFeishuConfig(DEFAULT_FEISHU_CONF));
  const [visibleFields, setVisibleFields] = useState<Record<string, boolean>>({});
  const [feishuLoading, setFeishuLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [xiaoyiConfig, setXiaoyiConfig] = useState<XiaoyiConfig>(DEFAULT_XIAOYI_CONF);
  const [xiaoyiDraft, setXiaoyiDraft] = useState<XiaoyiDraft>(draftFromXiaoyiConfig(DEFAULT_XIAOYI_CONF));
  const [xiaoyiVisibleFields, setXiaoyiVisibleFields] = useState<Record<string, boolean>>({});
  const [xiaoyiLoading, setXiaoyiLoading] = useState(false);
  const [xiaoyiSaving, setXiaoyiSaving] = useState(false);
  const [xiaoyiSaveError, setXiaoyiSaveError] = useState<string | null>(null);
  const [xiaoyiSuccess, setXiaoyiSuccess] = useState<string | null>(null);

  const fetchChannels = useCallback(async () => {
    setLoadState('loading');
    setError(null);
    try {
      const payload = await webRequest<{ channels?: unknown[] }>('channel.get');
      setChannels(buildChannels(payload?.channels));
      setLoadState('success');
      setLastUpdatedAt(new Date().toISOString());
    } catch (err) {
      setChannels(buildChannels([]));
      setLoadState('error');
      setError(err instanceof Error ? err.message : '获取 channels 失败');
    }
  }, []);

  useEffect(() => {
    void fetchChannels();
  }, [fetchChannels]);

  const fetchFeishuConfig = useCallback(async () => {
    setFeishuLoading(true);
    setSaveError(null);
    setSuccess(null);
    try {
      const payload = await webRequest<{ config?: unknown }>('channel.feishu.get_conf');
      const normalized = normalizeFeishuConfig(payload?.config);
      setFeishuConfig(normalized);
      setDraft(draftFromFeishuConfig(normalized));
      setVisibleFields({});
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : '获取飞书配置失败');
    } finally {
      setFeishuLoading(false);
    }
  }, []);

  const fetchXiaoyiConfig = useCallback(async () => {
    setXiaoyiLoading(true);
    setXiaoyiSaveError(null);
    setXiaoyiSuccess(null);
    try {
      const payload = await webRequest<{ config?: unknown }>('channel.xiaoyi.get_conf');
      const normalized = normalizeXiaoyiConfig(payload?.config);
      setXiaoyiConfig(normalized);
      setXiaoyiDraft(draftFromXiaoyiConfig(normalized));
      setXiaoyiVisibleFields({});
    } catch (err) {
      setXiaoyiSaveError(err instanceof Error ? err.message : '获取小艺配置失败');
    } finally {
      setXiaoyiLoading(false);
    }
  }, []);

  const handleSelectChannel = useCallback(
    (channelId: SupportedChannelId) => {
      setActiveChannelId(channelId);
    },
    [],
  );

  useEffect(() => {
    if (activeChannelId === 'feishu') {
      void fetchFeishuConfig();
      return;
    }
    if (activeChannelId === 'xiaoyi') {
      void fetchXiaoyiConfig();
    }
  }, [activeChannelId, fetchFeishuConfig, fetchXiaoyiConfig]);

  const statusText = useMemo(() => {
    const enabledCount = channels.filter((channel) => channel.enabled).length;
    if (loadState === 'loading') {
      return '加载中...';
    }
    if (loadState === 'error') {
      return '加载失败';
    }
    return `${enabledCount}/${channels.length} 已启用`;
  }, [channels, loadState]);

  const hasConfigChanges = useMemo(() => {
    const baseDraft = draftFromFeishuConfig(feishuConfig);
    return (
      baseDraft.enabled !== draft.enabled ||
      baseDraft.app_id !== draft.app_id ||
      baseDraft.app_secret !== draft.app_secret ||
      baseDraft.encrypt_key !== draft.encrypt_key ||
      baseDraft.verification_token !== draft.verification_token ||
      normalizeAllowFromText(baseDraft.allow_from).join('\n') !== normalizeAllowFromText(draft.allow_from).join('\n')
    );
  }, [draft, feishuConfig]);
  const hasXiaoyiConfigChanges = useMemo(() => {
    const baseDraft = draftFromXiaoyiConfig(xiaoyiConfig);
    return (
      baseDraft.enabled !== xiaoyiDraft.enabled ||
      baseDraft.ak !== xiaoyiDraft.ak ||
      baseDraft.sk !== xiaoyiDraft.sk ||
      baseDraft.agent_id !== xiaoyiDraft.agent_id ||
      baseDraft.ws_url1 !== xiaoyiDraft.ws_url1 ||
      baseDraft.ws_url2 !== xiaoyiDraft.ws_url2 ||
      baseDraft.enable_streaming !== xiaoyiDraft.enable_streaming
    );
  }, [xiaoyiConfig, xiaoyiDraft]);

  const handleFieldChange = <K extends keyof FeishuDraft>(key: K, value: FeishuDraft[K]) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    if (saveError) {
      setSaveError(null);
    }
    if (success) {
      setSuccess(null);
    }
  };

  const handleCancelConfig = () => {
    if (!hasConfigChanges) return;
    setDraft(draftFromFeishuConfig(feishuConfig));
    setSaveError(null);
    setSuccess(null);
  };

  const toggleFieldVisible = (field: keyof FeishuDraft) => {
    setVisibleFields((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const handleXiaoyiFieldChange = <K extends keyof XiaoyiDraft>(key: K, value: XiaoyiDraft[K]) => {
    setXiaoyiDraft((prev) => ({ ...prev, [key]: value }));
    if (xiaoyiSaveError) {
      setXiaoyiSaveError(null);
    }
    if (xiaoyiSuccess) {
      setXiaoyiSuccess(null);
    }
  };

  const handleCancelXiaoyiConfig = () => {
    if (!hasXiaoyiConfigChanges) return;
    setXiaoyiDraft(draftFromXiaoyiConfig(xiaoyiConfig));
    setXiaoyiSaveError(null);
    setXiaoyiSuccess(null);
  };

  const toggleXiaoyiFieldVisible = (field: keyof XiaoyiDraft) => {
    setXiaoyiVisibleFields((prev) => ({ ...prev, [field]: !prev[field] }));
  };

  const handleSaveConfig = async () => {
    if (!hasConfigChanges || saving) return;
    setSaving(true);
    setSaveError(null);
    try {
      const payload = buildFeishuPayload(draft);
      const result = await webRequest<{ config?: unknown }>('channel.feishu.set_conf', payload);
      const normalized = normalizeFeishuConfig(result?.config);
      setFeishuConfig(normalized);
      setDraft(draftFromFeishuConfig(normalized));
      setSuccess('飞书配置已保存');
    } catch (saveErr) {
      const message = saveErr instanceof Error ? saveErr.message : '保存失败，请稍后重试';
      setSaveError(message);
    } finally {
      setSaving(false);
    }
  };

  const handleSaveXiaoyiConfig = async () => {
    if (!hasXiaoyiConfigChanges || xiaoyiSaving) return;
    setXiaoyiSaving(true);
    setXiaoyiSaveError(null);
    try {
      const payload = buildXiaoyiPayload(xiaoyiDraft);
      const result = await webRequest<{ config?: unknown }>('channel.xiaoyi.set_conf', payload);
      const normalized = normalizeXiaoyiConfig(result?.config);
      setXiaoyiConfig(normalized);
      setXiaoyiDraft(draftFromXiaoyiConfig(normalized));
      setXiaoyiSuccess('小艺配置已保存');
    } catch (saveErr) {
      const message = saveErr instanceof Error ? saveErr.message : '保存失败，请稍后重试';
      setXiaoyiSaveError(message);
    } finally {
      setXiaoyiSaving(false);
    }
  };

  const isConfigRefreshing = feishuLoading || xiaoyiLoading;
  const configErrorNotice = useMemo(() => {
    return Array.from(new Set([saveError, xiaoyiSaveError].filter((message): message is string => Boolean(message)))).join('；');
  }, [saveError, xiaoyiSaveError]);
  useEffect(() => {
    if (!configErrorNotice) {
      return;
    }
    const timer = window.setTimeout(() => {
      setSaveError(null);
      setXiaoyiSaveError(null);
    }, 2000);
    return () => {
      window.clearTimeout(timer);
    };
  }, [configErrorNotice]);

  return (
    <div className="flex-1 min-h-0 relative">
      <div className="card w-full h-full flex flex-col">
        {configErrorNotice ? (
          <div className="pointer-events-none absolute top-3 left-1/2 -translate-x-1/2 z-20">
            <div className="bg-danger text-white px-4 py-2 rounded-lg shadow-lg animate-rise text-sm">
              {configErrorNotice}
            </div>
          </div>
        ) : null}
        <div className="flex items-center justify-between gap-4 mb-4">
          <div>
            <h2 className="text-lg font-semibold">频道管理</h2>
            <p className="text-sm text-text-muted mt-1">查看并管理频道服务</p>
          </div>
          <div className="flex items-center gap-2" />
        </div>

        {error ? (
          <div className="border border-[var(--border-danger)] bg-danger-subtle rounded-lg p-4 text-sm text-danger flex items-center justify-between">
            <span>获取失败：{error}</span>
            <button onClick={() => void fetchChannels()} className="btn !px-3 !py-1.5">
              重试
            </button>
          </div>
        ) : (
          <div className="flex-1 min-h-0 grid grid-cols-[minmax(0,3fr)_minmax(0,7fr)] gap-4">
            <section className="min-w-[260px] rounded-xl border border-border bg-card/70 backdrop-blur-sm shadow-sm flex flex-col min-h-0 overflow-hidden">
              <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <h3 className="text-sm font-medium text-text">频道列表</h3>
                    <p className="text-xs text-text-muted mt-1 mono">
                      频道个数：{statusText}，刷新时间：{formatTime(lastUpdatedAt)}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void fetchChannels()}
                    className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                    disabled={loadState === 'loading'}
                  >
                    {loadState === 'loading' ? '刷新中...' : '刷新'}
                  </button>
                </div>
              </div>
              <div className="overflow-auto flex-1 min-h-0 p-3">
                {loadState === 'loading' ? (
                  <div className="space-y-2">
                    <div className="h-10 rounded-lg border border-border bg-secondary/40" />
                    <div className="h-10 rounded-lg border border-border bg-secondary/30" />
                  </div>
                ) : (
                  <div className="space-y-2">
                    {channels.map((channel, index) => (
                      <button
                        type="button"
                        key={channel.channel_id}
                        onClick={() => handleSelectChannel(channel.channel_id)}
                        className={`w-full rounded-xl border px-4 py-3.5 text-left transition-colors ${
                          activeChannelId === channel.channel_id
                            ? 'border-accent bg-accent-subtle text-text'
                            : 'border-border bg-card text-text hover:bg-bg-hover'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-3">
                          <div className="flex items-center gap-3 min-w-0">
                            <span className="text-xs px-2.5 py-1 rounded-full border border-border bg-secondary text-text-muted font-medium">
                              #{index + 1}
                            </span>
                            <ChannelLogo channel={channel} />
                            <span className="text-sm font-medium text-text">{channel.label}</span>
                            <span className="mono text-xs px-2.5 py-1 rounded-md border border-border bg-secondary text-text-muted">
                              {channel.channel_id}
                            </span>
                          </div>
                          <span
                            className={`text-xs px-2.5 py-1 rounded-full border font-medium ${
                              channel.enabled
                                ? 'text-ok border-ok bg-ok-subtle'
                                : 'text-text-muted border-border bg-secondary'
                            }`}
                          >
                            {channel.enabled ? '已启用' : '未启用'}
                          </span>
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </section>

            <section className="min-h-0 flex">
                {activeChannelId === 'web' ? (
                  <div className="w-full h-full rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col">
                    <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                      <div className="flex items-center gap-3">
                        <ChannelHeaderLogo channelId="web" label="网页" />
                        <div>
                          <h4 className="text-sm font-medium text-text">网页频道参数配置</h4>
                          <p className="text-xs text-text-muted mt-1">配置网页频道服务相关参数</p>
                        </div>
                      </div>
                    </div>
                    <div className="p-4 text-sm text-text-muted flex-1 overflow-auto flex items-center justify-center text-center">
                      网页频道暂无配置，点击其他频道加载配置。
                    </div>
                  </div>
                ) : null}

                {activeChannelId === 'xiaoyi' ? (
                  <div className="w-full h-full rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col">
                    <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                      <div className="flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <ChannelHeaderLogo channelId="xiaoyi" label="小艺" />
                          <div>
                            <h4 className="text-sm font-medium text-text">小艺频道参数配置</h4>
                            <p className="text-xs text-text-muted mt-1">配置小艺频道服务相关参数</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => void fetchXiaoyiConfig()}
                            disabled={xiaoyiSaving || isConfigRefreshing}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {xiaoyiLoading ? '刷新中...' : '刷新'}
                          </button>
                          <button
                            type="button"
                            onClick={handleCancelXiaoyiConfig}
                            disabled={!hasXiaoyiConfigChanges || xiaoyiSaving}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            取消
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleSaveXiaoyiConfig()}
                            disabled={!hasXiaoyiConfigChanges || xiaoyiSaving || !isConnected}
                            className="btn primary !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {xiaoyiSaving ? '保存中...' : '保存'}
                          </button>
                        </div>
                      </div>
                    </div>

                    {xiaoyiSuccess ? (
                      <div className="mx-4 mt-4 rounded-md border border-[var(--border-ok)] bg-ok-subtle px-3 py-2 text-sm text-ok">
                        {xiaoyiSuccess}
                      </div>
                    ) : null}

                    <div className="p-4 pt-3 flex-1 overflow-auto">
                      {xiaoyiLoading ? (
                        <div className="text-sm text-text-muted">正在加载小艺配置...</div>
                      ) : (
                        <table className="w-full text-sm">
                          <tbody>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">enabled</td>
                              <td className="px-4 py-2.5 align-middle">
                                <button
                                  type="button"
                                  role="switch"
                                  aria-checked={xiaoyiDraft.enabled}
                                  onClick={() => handleXiaoyiFieldChange('enabled', !xiaoyiDraft.enabled)}
                                  className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                                    xiaoyiDraft.enabled ? 'bg-ok' : 'bg-secondary'
                                  }`}
                                >
                                  <span
                                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200 ${
                                      xiaoyiDraft.enabled ? 'translate-x-4' : 'translate-x-0'
                                    }`}
                                  />
                                </button>
                              </td>
                            </tr>
                            {(['ak', 'sk', 'agent_id', 'ws_url1', 'ws_url2'] as const).map((field) => (
                              <tr key={field} className="border-t border-border first:border-t-0 even:bg-secondary/10">
                                <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">{field}</td>
                                <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                  <div className="relative">
                                    <input
                                      type={isSensitiveXiaoyiField(field) && !xiaoyiVisibleFields[field] ? 'password' : 'text'}
                                      value={xiaoyiDraft[field]}
                                      onChange={(e) => handleXiaoyiFieldChange(field, e.target.value)}
                                      placeholder="请输入配置值"
                                      className={`w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent ${
                                        isSensitiveXiaoyiField(field) ? 'pr-10' : ''
                                      } ${isReadonlyXiaoyiField(field) ? 'bg-secondary/30 text-text-muted focus:border-border' : ''}`}
                                      readOnly={isReadonlyXiaoyiField(field)}
                                    />
                                    {isSensitiveXiaoyiField(field) ? (
                                      <button
                                        type="button"
                                        onClick={() => toggleXiaoyiFieldVisible(field)}
                                        className="channels-panel__visibility-toggle"
                                        aria-label={xiaoyiVisibleFields[field] ? '隐藏明文' : '显示明文'}
                                        title={xiaoyiVisibleFields[field] ? '隐藏明文' : '显示明文'}
                                      >
                                        <VisibilityIcon visible={Boolean(xiaoyiVisibleFields[field])} />
                                      </button>
                                    ) : null}
                                  </div>
                                </td>
                              </tr>
                            ))}
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">enable_streaming</td>
                              <td className="px-4 py-2.5 align-middle">
                                <button
                                  type="button"
                                  role="switch"
                                  aria-checked={xiaoyiDraft.enable_streaming}
                                  onClick={() => handleXiaoyiFieldChange('enable_streaming', !xiaoyiDraft.enable_streaming)}
                                  className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                                    xiaoyiDraft.enable_streaming ? 'bg-ok' : 'bg-secondary'
                                  }`}
                                >
                                  <span
                                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200 ${
                                      xiaoyiDraft.enable_streaming ? 'translate-x-4' : 'translate-x-0'
                                    }`}
                                  />
                                </button>
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                ) : null}

                {activeChannelId === 'feishu' ? (
                  <div className="w-full h-full rounded-xl border border-border bg-card/70 backdrop-blur-sm overflow-hidden shadow-sm flex flex-col">
                    <div className="px-4 py-3 bg-secondary/30 border-b border-border">
                      <div className="flex items-center justify-between gap-4">
                        <div className="flex items-center gap-3">
                          <ChannelHeaderLogo channelId="feishu" label="飞书" />
                          <div>
                            <h4 className="text-sm font-medium text-text">飞书频道参数配置</h4>
                            <p className="text-xs text-text-muted mt-1">配置飞书频道服务相关参数</p>
                          </div>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => void fetchFeishuConfig()}
                            disabled={saving || isConfigRefreshing}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {feishuLoading ? '刷新中...' : '刷新'}
                          </button>
                          <button
                            type="button"
                            onClick={handleCancelConfig}
                            disabled={!hasConfigChanges || saving}
                            className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            取消
                          </button>
                          <button
                            type="button"
                            onClick={() => void handleSaveConfig()}
                            disabled={!hasConfigChanges || saving || !isConnected}
                            className="btn primary !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                          >
                            {saving ? '保存中...' : '保存'}
                          </button>
                        </div>
                      </div>
                    </div>

                    {success ? (
                      <div className="mx-4 mt-4 rounded-md border border-[var(--border-ok)] bg-ok-subtle px-3 py-2 text-sm text-ok">
                        {success}
                      </div>
                    ) : null}

                    <div className="p-4 pt-3 flex-1 overflow-auto">
                      {feishuLoading ? (
                        <div className="text-sm text-text-muted">正在加载飞书配置...</div>
                      ) : (
                        <table className="w-full text-sm">
                          <tbody>
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">enabled</td>
                              <td className="px-4 py-2.5 align-middle">
                                <button
                                  type="button"
                                  role="switch"
                                  aria-checked={draft.enabled}
                                  onClick={() => handleFieldChange('enabled', !draft.enabled)}
                                  className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 focus:outline-none ${
                                    draft.enabled ? 'bg-ok' : 'bg-secondary'
                                  }`}
                                >
                                  <span
                                    className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition duration-200 ${
                                      draft.enabled ? 'translate-x-4' : 'translate-x-0'
                                    }`}
                                  />
                                </button>
                              </td>
                            </tr>
                            {(['app_id', 'app_secret', 'encrypt_key', 'verification_token'] as const).map((field) => (
                              <tr key={field} className="border-t border-border first:border-t-0 even:bg-secondary/10">
                                <td className="px-4 py-2.5 align-middle mono text-xs text-text-muted w-[32%]">{field}</td>
                                <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                  <div className="relative">
                                    <input
                                      type={isSensitiveField(field) && !visibleFields[field] ? 'password' : 'text'}
                                      value={draft[field]}
                                      onChange={(e) => handleFieldChange(field, e.target.value)}
                                      placeholder="请输入配置值"
                                      className={`w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent ${
                                        isSensitiveField(field) ? 'pr-10' : ''
                                      }`}
                                    />
                                    {isSensitiveField(field) ? (
                                      <button
                                        type="button"
                                        onClick={() => toggleFieldVisible(field)}
                                        className="channels-panel__visibility-toggle"
                                        aria-label={visibleFields[field] ? '隐藏明文' : '显示明文'}
                                        title={visibleFields[field] ? '隐藏明文' : '显示明文'}
                                      >
                                        <VisibilityIcon visible={Boolean(visibleFields[field])} />
                                      </button>
                                    ) : null}
                                  </div>
                                </td>
                              </tr>
                            ))}
                            <tr className="border-t border-border first:border-t-0 even:bg-secondary/10">
                              <td className="px-4 py-2.5 align-top mono text-xs text-text-muted w-[32%]">allow_from</td>
                              <td className="px-4 py-2.5 break-all text-[13px] align-middle">
                                <textarea
                                  value={draft.allow_from}
                                  onChange={(e) => handleFieldChange('allow_from', e.target.value)}
                                  placeholder="每行一个 ID（也支持逗号分隔）"
                                  rows={4}
                                  className="w-full rounded-md border border-border bg-bg px-3 py-2 text-[13px] outline-none focus:border-accent resize-y"
                                />
                              </td>
                            </tr>
                          </tbody>
                        </table>
                      )}
                    </div>
                  </div>
                ) : null}
            </section>
          </div>
        )}
      </div>
    </div>
  );
}
