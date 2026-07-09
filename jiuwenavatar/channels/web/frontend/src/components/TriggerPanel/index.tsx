/**
 * TriggerPanel — 统一触发器管理
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { webRequest } from '../../services/webClient';
import { useAvatarStore } from '../../stores/avatarStore';
import { PlatformPageLayout, PlatformEmpty } from '../AvatarPlatform/PlatformPageLayout';
import '../AvatarPlatform/AvatarPlatform.css';

type TriggerType = 'cron' | 'heartbeat' | 'webhook' | 'event';

interface TriggerConfig {
  id: string;
  name: string;
  type: TriggerType;
  avatar_id: string;
  enabled: boolean;
  cron_expr?: string;
  interval_seconds?: number;
  webhook_path?: string;
  event_source?: string;
  event_type?: string;
  trigger_prompt: string;
  timezone?: string;
  last_triggered_at?: string;
  last_error?: string;
}

const TYPE_CLASS: Record<TriggerType, string> = {
  cron: 'trigger-type-badge--cron',
  heartbeat: 'trigger-type-badge--heartbeat',
  webhook: 'trigger-type-badge--webhook',
  event: 'trigger-type-badge--event',
};

const TYPE_I18N: Record<TriggerType, string> = {
  cron: 'trigger.typeCron',
  heartbeat: 'trigger.typeHeartbeat',
  webhook: 'trigger.typeWebhook',
  event: 'trigger.typeEvent',
};

export function TriggerPanel() {
  const { t } = useTranslation();
  const typeLabel = (type: TriggerType) => t(TYPE_I18N[type]);
  const [triggers, setTriggers] = useState<TriggerConfig[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [editing, setEditing] = useState<TriggerConfig | null>(null);
  const { avatars, fetchAvatars } = useAvatarStore();

  const sendRequest = useCallback(
    (method: string, params?: Record<string, unknown>) => webRequest(method, params),
    [],
  );

  const fetchTriggers = useCallback(async () => {
    setLoading(true);
    try {
      const res = await webRequest<{ triggers?: TriggerConfig[] }>('triggers.list');
      setTriggers(res?.triggers || []);
    } catch { /* ignore */ }
    setLoading(false);
  }, []);

  useEffect(() => {
    fetchTriggers();
    fetchAvatars(sendRequest);
  }, [fetchTriggers, fetchAvatars, sendRequest]);

  const workflow = [
    { num: 1, label: t('platform.workflow.createAvatar', '创建分身') },
    { num: 2, label: t('platform.workflow.setupTrigger', '配置触发器'), active: true },
    { num: 3, label: t('platform.workflow.viewReport', '查看报告') },
  ];

  const typeDetail = (tr: TriggerConfig) => {
    if (tr.type === 'cron') return `Cron · ${tr.cron_expr}`;
    if (tr.type === 'heartbeat') return `${t('trigger.interval', '间隔')} · ${tr.interval_seconds}s`;
    if (tr.type === 'webhook') return tr.webhook_path;
    return `${tr.event_source}/${tr.event_type}`;
  };

  const avatarName = (id: string) => avatars.find((a) => a.id === id)?.name || id;

  return (
    <PlatformPageLayout
      title={t('trigger.title', '触发器')}
      subtitle={t('trigger.pageSubtitle', '为分身配置定时、心跳、Webhook 或事件触发器，在合适时机自动派发任务。')}
      workflow={workflow}
      toolbar={
        <button type="button" className="avatar-platform__btn avatar-platform__btn--primary" onClick={() => setShowCreate(true)}>
          {t('trigger.create', '新建触发器')}
        </button>
      }
    >
      {loading ? (
        <div className="avatar-platform__loading">{t('trigger.loading', '加载中...')}</div>
      ) : triggers.length === 0 ? (
        <PlatformEmpty
          title={t('trigger.noTriggers', '暂无触发器')}
          description={t('trigger.noTriggersHint', '先创建分身，再为其添加 Cron 定时或 Webhook 触发器，分身将按计划自动执行任务。')}
        />
      ) : (
        <div className="avatar-platform__list">
          {triggers.map((tr) => (
            <div key={tr.id} className="avatar-platform__card">
              <div className="avatar-platform__card-header">
                <div>
                  <div className="flex items-center gap-2 flex-wrap">
                    <h3 className="avatar-platform__card-title">{tr.name}</h3>
                    <span className={`trigger-type-badge ${TYPE_CLASS[tr.type]}`}>{typeLabel(tr.type)}</span>
                    {!tr.enabled && <span className="avatar-platform__tag avatar-platform__tag--muted">{t('trigger.paused', '已暂停')}</span>}
                  </div>
                  <p className="avatar-platform__card-meta mt-1">
                    {typeDetail(tr)} · {t('trigger.avatar', '分身')}: {avatarName(tr.avatar_id)}
                  </p>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button
                    type="button"
                    className="avatar-platform__btn avatar-platform__btn--ghost"
                    onClick={() => setEditing(tr)}
                  >
                    {t('trigger.edit', '编辑')}
                  </button>
                  <button
                    type="button"
                    className="avatar-platform__btn avatar-platform__btn--ghost"
                    onClick={async () => {
                      await webRequest('triggers.update', { trigger_id: tr.id, enabled: !tr.enabled });
                      fetchTriggers();
                    }}
                  >
                    {tr.enabled ? t('trigger.pause', '暂停') : t('trigger.resume', '恢复')}
                  </button>
                  <button
                    type="button"
                    className="avatar-platform__btn avatar-platform__btn--danger"
                    onClick={async () => {
                      if (!confirm(t('trigger.confirmDelete', '确认删除？'))) return;
                      await webRequest('triggers.delete', { trigger_id: tr.id });
                      fetchTriggers();
                    }}
                  >
                    {t('trigger.delete', '删除')}
                  </button>
                </div>
              </div>
              <p className="avatar-platform__card-desc line-clamp-2">{tr.trigger_prompt}</p>
              {tr.last_triggered_at && (
                <p className="avatar-platform__card-meta mt-2">{t('trigger.lastTriggered', '最后触发')}: {new Date(tr.last_triggered_at).toLocaleString()}</p>
              )}
            </div>
          ))}
        </div>
      )}
      {(showCreate || editing) && (
        <TriggerFormModal
          avatars={avatars}
          existing={editing}
          onClose={() => { setShowCreate(false); setEditing(null); fetchTriggers(); }}
        />
      )}
    </PlatformPageLayout>
  );
}

function TriggerFormModal({
  avatars,
  existing,
  onClose,
}: {
  avatars: { id: string; name: string }[];
  existing?: TriggerConfig | null;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const isEdit = Boolean(existing);
  const [type, setType] = useState<TriggerType>(existing?.type || 'cron');
  const [name, setName] = useState(existing?.name || '');
  const [avatarId, setAvatarId] = useState(existing?.avatar_id || avatars[0]?.id || '');
  const [prompt, setPrompt] = useState(existing?.trigger_prompt || '');
  const [cronExpr, setCronExpr] = useState(existing?.cron_expr || '0 9 * * 1-5');
  const [timezone, setTimezone] = useState(existing?.timezone || 'Asia/Shanghai');
  const [intervalSeconds, setIntervalSeconds] = useState(
    existing?.interval_seconds != null ? String(existing.interval_seconds) : '3600',
  );
  const [webhookPath, setWebhookPath] = useState(existing?.webhook_path || '/webhook/trigger');
  const [eventSource, setEventSource] = useState(existing?.event_source || '');
  const [eventType, setEventType] = useState(existing?.event_type || '');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const fields: Record<string, unknown> = {
        name: name || `${type} trigger`,
        trigger_prompt: prompt,
        avatar_id: avatarId,
      };
      if (type === 'cron') {
        fields.cron_expr = cronExpr;
        fields.timezone = timezone;
      }
      if (type === 'heartbeat') fields.interval_seconds = parseFloat(intervalSeconds);
      if (type === 'webhook') fields.webhook_path = webhookPath;
      if (type === 'event') { fields.event_source = eventSource; fields.event_type = eventType; }

      if (isEdit && existing) {
        await webRequest('triggers.update', { trigger_id: existing.id, ...fields });
      } else {
        await webRequest('triggers.create', { type, ...fields });
      }
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : t('trigger.saveFailed', '保存失败'));
      setSubmitting(false);
    }
  };

  const types: TriggerType[] = ['cron', 'heartbeat', 'webhook', 'event'];

  return (
    <div className="avatar-platform-modal">
      <div className="avatar-platform-modal__backdrop" onClick={onClose} />
      <div className="avatar-platform-modal__panel">
        <h3 className="avatar-platform-modal__title">
          {isEdit ? t('trigger.editTitle', '编辑触发器') : t('trigger.createTitle', '新建触发器')}
        </h3>
        {/* 类型在编辑时不可更改（更改类型需重建对应配置） */}
        <div className="flex flex-wrap gap-2 mt-4">
          {types.map((tt) => (
            <button
              key={tt}
              type="button"
              disabled={isEdit}
              className={`avatar-platform__btn avatar-platform__btn--ghost${type === tt ? ' !border-[var(--accent)] !bg-[var(--accent-subtle)]' : ''}${isEdit && type !== tt ? ' opacity-40' : ''}`}
              onClick={() => !isEdit && setType(tt)}
            >
              {t(TYPE_I18N[tt])}
            </button>
          ))}
        </div>
        <div className="avatar-platform__field">
          <label className="avatar-platform__field-label">{t('trigger.name', '名称')}</label>
          <input className="avatar-platform__input" placeholder={t('trigger.namePlaceholder', '')} value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="avatar-platform__field">
          <label className="avatar-platform__field-label">{t('trigger.avatarId', '关联分身')}</label>
          <select className="avatar-platform__input" value={avatarId} onChange={(e) => setAvatarId(e.target.value)}>
            {avatars.length === 0 ? <option value="">{t('trigger.noAvatarOption', '请先创建分身')}</option> : null}
            {avatars.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        </div>
        {type === 'cron' && (
          <>
            <div className="avatar-platform__field">
              <label className="avatar-platform__field-label">{t('trigger.cronExpr', 'Cron 表达式')}</label>
              <input className="avatar-platform__input font-mono" value={cronExpr} onChange={(e) => setCronExpr(e.target.value)} />
            </div>
            <div className="avatar-platform__field">
              <label className="avatar-platform__field-label">{t('trigger.timezone', '时区')}</label>
              <input className="avatar-platform__input font-mono" value={timezone} onChange={(e) => setTimezone(e.target.value)} />
            </div>
          </>
        )}
        {type === 'heartbeat' && (
          <div className="avatar-platform__field">
            <label className="avatar-platform__field-label">{t('trigger.interval', '间隔（秒）')}</label>
            <input type="number" className="avatar-platform__input" value={intervalSeconds} onChange={(e) => setIntervalSeconds(e.target.value)} />
          </div>
        )}
        {type === 'webhook' && (
          <div className="avatar-platform__field">
            <label className="avatar-platform__field-label">{t('trigger.webhookPath', 'Webhook 路径')}</label>
            <input className="avatar-platform__input font-mono" value={webhookPath} onChange={(e) => setWebhookPath(e.target.value)} />
          </div>
        )}
        {type === 'event' && (
          <>
            <div className="avatar-platform__field">
              <label className="avatar-platform__field-label">{t('trigger.eventSource', '事件来源')}</label>
              <input className="avatar-platform__input" value={eventSource} onChange={(e) => setEventSource(e.target.value)} />
            </div>
            <div className="avatar-platform__field">
              <label className="avatar-platform__field-label">{t('trigger.eventType', '事件类型')}</label>
              <input className="avatar-platform__input" value={eventType} onChange={(e) => setEventType(e.target.value)} />
            </div>
          </>
        )}
        <div className="avatar-platform__field">
          <label className="avatar-platform__field-label">{t('trigger.prompt', '触发 Prompt')}</label>
          <textarea className="avatar-platform__input min-h-[80px]" rows={3} placeholder={t('trigger.promptPlaceholder', '')} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
        </div>
        {error && <p className="avatar-platform__card-meta" style={{ color: 'var(--danger, #ef4444)' }}>{error}</p>}
        <div className="avatar-platform-modal__actions">
          <button type="button" className="avatar-platform__btn avatar-platform__btn--ghost" onClick={onClose}>{t('trigger.cancel', '取消')}</button>
          <button
            type="button"
            className="avatar-platform__btn avatar-platform__btn--primary"
            disabled={!prompt || !avatarId || submitting}
            onClick={handleSubmit}
          >
            {isEdit ? t('trigger.save', '保存') : t('trigger.create', '创建')}
          </button>
        </div>
      </div>
    </div>
  );
}
