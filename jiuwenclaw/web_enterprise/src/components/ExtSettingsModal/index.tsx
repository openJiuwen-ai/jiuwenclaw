/**
 * 请求扩展字段设置控件（按钮 + Modal）。
 *
 * 用法：在 InputArea 工具条中放置 <ExtSettingsControl />，本组件自带：
 *   - 一个滑块图标按钮（点击打开 Modal）
 *   - 一个 Modal（编辑 user_id / group_id / bot_id / 自定义 KV）
 *   - 保存逻辑（写 store + 触发 WS 重连）
 *
 * 所有透传字段最终通过 WS 连接 URL query 发送给后端，由 jiuwenclaw.request_ext
 * 按 JIUWENCLAW_REQUEST_EXT_FORWARD_HEADERS 抽取并贯穿到 Agent rail。
 */

import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';

import {
  EXT_CUSTOM_KEY_WHITELIST,
  useExtSettingsStore,
  type ExtCustomKV,
  type ExtSettingsSnapshot,
} from '../../stores';

function SlidersIcon({ className }: { className?: string }) {
  return (
    <svg
      className={className}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
    >
      <line x1="4" y1="7" x2="20" y2="7" />
      <circle cx="9" cy="7" r="2.2" fill="currentColor" stroke="none" />
      <line x1="4" y1="12" x2="20" y2="12" />
      <circle cx="15" cy="12" r="2.2" fill="currentColor" stroke="none" />
      <line x1="4" y1="17" x2="20" y2="17" />
      <circle cx="9" cy="17" r="2.2" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function ExtSettingsControl() {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="chat-input-btn"
        title={t('extSettings.title')}
        aria-label={t('extSettings.title')}
      >
        <SlidersIcon className="chat-input-btn-icon" />
      </button>
      {open && <ExtSettingsModal onClose={() => setOpen(false)} />}
    </>
  );
}

interface ExtSettingsModalProps {
  onClose: () => void;
}

function ExtSettingsModal({ onClose }: ExtSettingsModalProps) {
  const { t } = useTranslation();
  const snapshot = useExtSettingsStore((state) => ({
    userId: state.userId,
    groupId: state.groupId,
    botId: state.botId,
    customKVs: state.customKVs,
  }));
  const saveAndApply = useExtSettingsStore((state) => state.saveAndApply);

  const [userId, setUserId] = useState(snapshot.userId);
  const [groupId, setGroupId] = useState(snapshot.groupId);
  const [botId, setBotId] = useState(snapshot.botId);
  const [customKVs, setCustomKVs] = useState<ExtCustomKV[]>(snapshot.customKVs);

  // Esc 关闭
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [onClose]);

  // 当前已使用的 key，下次添加默认选未使用的第一个；保留同 key 多行的可能（虽然不推荐）。
  const firstUnusedKey = useMemo(() => {
    const used = new Set(customKVs.map((kv) => kv.key));
    return EXT_CUSTOM_KEY_WHITELIST.find((k) => !used.has(k)) ?? EXT_CUSTOM_KEY_WHITELIST[0];
  }, [customKVs]);

  const handleAddRow = () => {
    setCustomKVs((prev) => [...prev, { key: firstUnusedKey, value: '' }]);
  };

  const handleRemoveRow = (index: number) => {
    setCustomKVs((prev) => prev.filter((_, i) => i !== index));
  };

  const handleKeyChange = (index: number, key: string) => {
    setCustomKVs((prev) => prev.map((kv, i) => (i === index ? { ...kv, key } : kv)));
  };

  const handleValueChange = (index: number, value: string) => {
    setCustomKVs((prev) => prev.map((kv, i) => (i === index ? { ...kv, value } : kv)));
  };

  const handleSave = () => {
    const next: ExtSettingsSnapshot = {
      userId,
      groupId,
      botId,
      customKVs,
    };
    saveAndApply(next);
    onClose();
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div
        className="relative w-full max-w-2xl max-h-[85vh] overflow-hidden rounded-xl flex flex-col animate-rise"
        style={{
          backgroundColor: 'var(--card)',
          boxShadow: 'var(--shadow-xl)',
        }}
      >
        {/* 标题栏 */}
        <div
          className="px-6 py-4 flex items-center gap-3"
          style={{
            backgroundColor: 'var(--panel-strong)',
            borderBottom: '1px solid var(--border)',
          }}
        >
          <div
            className="w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0"
            style={{
              background: 'linear-gradient(135deg, var(--accent), var(--accent-2))',
            }}
          >
            <SlidersIcon className="w-5 h-5 text-white" />
          </div>
          <div className="flex-1 min-w-0">
            <h2
              className="text-base font-semibold truncate"
              style={{ color: 'var(--fg)' }}
            >
              {t('extSettings.title')}
            </h2>
            <p
              className="text-xs mt-0.5 truncate"
              style={{ color: 'var(--fg-muted)' }}
            >
              {t('extSettings.subtitle')}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-md transition-colors"
            style={{ color: 'var(--fg-muted)' }}
            title={t('common.close')}
            aria-label={t('common.close')}
          >
            <svg
              className="w-5 h-5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* 正文 */}
        <div
          className="flex-1 overflow-y-auto px-6 py-5 space-y-5"
          style={{ color: 'var(--fg)' }}
        >
          {/* 固定字段 */}
          <div className="space-y-3">
            <div>
              <label
                className="block text-xs font-medium mb-1.5"
                style={{ color: 'var(--fg-muted)' }}
              >
                {t('extSettings.userIdLabel')}
              </label>
              <input
                type="text"
                value={userId}
                onChange={(e) => setUserId(e.target.value)}
                readOnly
                title={t('extSettings.injectedHint', '由所在应用注入，不可手动修改')}
                placeholder={t('extSettings.userIdPlaceholder')}
                className="w-full px-3 py-2 rounded-md text-sm outline-none transition-colors"
                style={{
                  backgroundColor: 'rgba(128,128,128,0.15)',
                  border: '1px solid var(--border)',
                  color: 'var(--fg-muted)',
                  cursor: 'not-allowed',
                }}
              />
            </div>
            <div>
              <label
                className="block text-xs font-medium mb-1.5"
                style={{ color: 'var(--fg-muted)' }}
              >
                {t('extSettings.groupIdLabel')}
              </label>
              <input
                type="text"
                value={groupId}
                onChange={(e) => setGroupId(e.target.value)}
                readOnly
                title={t('extSettings.injectedHint', '由所在应用注入，不可手动修改')}
                placeholder={t('extSettings.groupIdPlaceholder')}
                className="w-full px-3 py-2 rounded-md text-sm outline-none transition-colors"
                style={{
                  backgroundColor: 'rgba(128,128,128,0.15)',
                  border: '1px solid var(--border)',
                  color: 'var(--fg-muted)',
                  cursor: 'not-allowed',
                }}
              />
            </div>
            <div>
              <label
                className="block text-xs font-medium mb-1.5"
                style={{ color: 'var(--fg-muted)' }}
              >
                {t('extSettings.botIdLabel')}
              </label>
              <input
                type="text"
                value={botId}
                onChange={(e) => setBotId(e.target.value)}
                readOnly
                title={t('extSettings.injectedHint', '由所在应用注入，不可手动修改')}
                placeholder={t('extSettings.botIdPlaceholder')}
                className="w-full px-3 py-2 rounded-md text-sm outline-none transition-colors"
                style={{
                  backgroundColor: 'rgba(128,128,128,0.15)',
                  border: '1px solid var(--border)',
                  color: 'var(--fg-muted)',
                  cursor: 'not-allowed',
                }}
              />
            </div>
          </div>

          {/* 分隔线 */}
          <div style={{ borderTop: '1px solid var(--border)' }} />

          {/* 自定义键值对 */}
          <div className="space-y-3">
            <div className="flex items-baseline justify-between">
              <label
                className="text-sm font-medium"
                style={{ color: 'var(--fg)' }}
              >
                {t('extSettings.customKvLabel')}
              </label>
              <span className="text-xs" style={{ color: 'var(--fg-muted)' }}>
                {t('extSettings.customKvHint')}
              </span>
            </div>

            {customKVs.length === 0 ? (
              <div
                className="text-center py-4 text-xs rounded-md"
                style={{
                  color: 'var(--fg-muted)',
                  border: '1px dashed var(--border)',
                }}
              >
                {t('extSettings.customKvEmpty')}
              </div>
            ) : (
              <div className="space-y-2">
                {customKVs.map((kv, index) => (
                  <div key={index} className="flex items-center gap-2">
                    <select
                      value={kv.key}
                      onChange={(e) => handleKeyChange(index, e.target.value)}
                      className="px-2 py-1.5 rounded-md text-sm outline-none w-44 flex-shrink-0"
                      style={{
                        backgroundColor: 'var(--input-bg)',
                        border: '1px solid var(--border)',
                        color: 'var(--fg)',
                      }}
                    >
                      {EXT_CUSTOM_KEY_WHITELIST.map((k) => (
                        <option key={k} value={k}>
                          {k}
                        </option>
                      ))}
                    </select>
                    <input
                      type="text"
                      value={kv.value}
                      onChange={(e) => handleValueChange(index, e.target.value)}
                      placeholder={t('extSettings.customKvValuePlaceholder')}
                      className="flex-1 min-w-0 px-3 py-1.5 rounded-md text-sm outline-none"
                      style={{
                        backgroundColor: 'var(--input-bg)',
                        border: '1px solid var(--border)',
                        color: 'var(--fg)',
                      }}
                    />
                    <button
                      type="button"
                      onClick={() => handleRemoveRow(index)}
                      className="p-1.5 rounded-md transition-colors flex-shrink-0"
                      style={{ color: 'var(--fg-muted)' }}
                      title={t('common.delete')}
                      aria-label={t('common.delete')}
                    >
                      <svg
                        className="w-4 h-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        strokeWidth={1.8}
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M1 7h22M9 7V4a2 2 0 012-2h2a2 2 0 012 2v3"
                        />
                      </svg>
                    </button>
                  </div>
                ))}
              </div>
            )}

            <button
              type="button"
              onClick={handleAddRow}
              className={clsx(
                'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-sm transition-colors',
              )}
              style={{
                backgroundColor: 'var(--panel-strong)',
                color: 'var(--fg)',
                border: '1px solid var(--border)',
              }}
            >
              <svg
                className="w-4 h-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                strokeWidth={2}
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
              </svg>
              {t('extSettings.addRow')}
            </button>
          </div>
        </div>

        {/* 底部按钮区 */}
        <div
          className="px-6 py-3 flex items-center justify-end gap-2"
          style={{
            backgroundColor: 'var(--panel-strong)',
            borderTop: '1px solid var(--border)',
          }}
        >
          <span
            className="text-xs mr-auto"
            style={{ color: 'var(--fg-muted)' }}
          >
            {t('extSettings.saveHint')}
          </span>
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-1.5 rounded-md text-sm transition-colors"
            style={{
              backgroundColor: 'transparent',
              color: 'var(--fg)',
              border: '1px solid var(--border)',
            }}
          >
            {t('common.cancel')}
          </button>
          <button
            type="button"
            onClick={handleSave}
            className="px-4 py-1.5 rounded-md text-sm font-medium transition-colors"
            style={{
              background: 'linear-gradient(135deg, var(--accent), var(--accent-2))',
              color: '#fff',
            }}
          >
            {t('extSettings.save')}
          </button>
        </div>
      </div>
    </div>
  );
}
