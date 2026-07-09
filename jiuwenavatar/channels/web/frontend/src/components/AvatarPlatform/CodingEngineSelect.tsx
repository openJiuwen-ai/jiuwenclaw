/**
 * CodingEngineSelect — 编码后端选择（Jiuwen Coding / Claude Code / Codex）
 */

import { useTranslation } from 'react-i18next';

export type CodingEngine = 'jiuwen-coding' | 'claude-code' | 'codex';

export interface CliInstallStatus {
  running: boolean;
  last_detail: string;
  success: boolean;
  failed: boolean;
}

export interface CodingEngineStatus {
  kind: string;
  display_name: string;
  configured: boolean;
  selectable: boolean;
  reason: string;
  cli_install_status?: CliInstallStatus | null;
}

export type CodingEngineStatusMap = Partial<Record<CodingEngine, CodingEngineStatus>>;

const ENGINE_META: Record<CodingEngine, { nameKey: string; descKey: string }> = {
  'jiuwen-coding': { nameKey: 'avatar.codingEngine.jiuwen', descKey: 'avatar.codingEngine.jiuwenDesc' },
  'claude-code': { nameKey: 'avatar.codingEngine.claude', descKey: 'avatar.codingEngine.claudeDesc' },
  codex: { nameKey: 'avatar.codingEngine.codex', descKey: 'avatar.codingEngine.codexDesc' },
};

const REASON_I18N: Record<string, string> = {
  anthropic_not_configured: 'avatar.codingEngine.anthropicNotConfigured',
  openai_not_configured: 'avatar.codingEngine.openaiNotConfigured',
  credentials_not_configured: 'avatar.codingEngine.requiresConfig',
};

interface CodingEngineSelectProps {
  value: CodingEngine;
  options: CodingEngine[];
  onChange: (value: CodingEngine) => void;
  engineStatus?: CodingEngineStatusMap;
  onRetryInstall?: (engine: CodingEngine) => void;
  onGoToConfig?: () => void;
}

export function pickSelectableCodingEngine(
  engines: CodingEngine[],
  engineStatus: CodingEngineStatusMap | undefined,
  preferred?: CodingEngine | null,
): CodingEngine {
  const isSelectable = (engine: CodingEngine) => {
    const status = engineStatus?.[engine];
    return !status || status.selectable;
  };
  if (preferred && engines.includes(preferred) && isSelectable(preferred)) {
    return preferred;
  }
  const first = engines.find(isSelectable);
  return first || engines[0] || 'jiuwen-coding';
}

export function CodingEngineSelect({ value, options, onChange, engineStatus, onRetryInstall, onGoToConfig }: CodingEngineSelectProps) {
  const { t } = useTranslation();

  if (options.length === 0) return null;

  const reasonText = (reason: string) => {
    const key = REASON_I18N[reason] || REASON_I18N.credentials_not_configured;
    return t(key);
  };

  const renderCliInstallStatus = (engine: CodingEngine, cliStatus: CliInstallStatus | null | undefined) => {
    if (!cliStatus) return null;
    
    if (cliStatus.running) {
      return (
        <span className="coding-engine-select__cli-status coding-engine-select__cli-status--running">
          <span className="loading-spinner" /> {t('avatar.codingEngine.cliInstalling', '正在安装 CLI...')}
        </span>
      );
    }
    
    if (cliStatus.success) {
      return (
        <span className="coding-engine-select__cli-status coding-engine-select__cli-status--success">
          ✓ {t('avatar.codingEngine.cliInstalled', 'CLI 已安装')}
        </span>
      );
    }
    
    if (cliStatus.failed) {
      return (
        <span className="coding-engine-select__cli-status coding-engine-select__cli-status--failed">
          <span className="cli-error-text">
            ✗ {t('avatar.codingEngine.cliInstallFailed', 'CLI 安装失败')}
            {cliStatus.last_detail && <span className="cli-error-detail">: {cliStatus.last_detail}</span>}
          </span>
          {onRetryInstall && (
            <button
              type="button"
              className="coding-engine-select__retry-btn"
              onClick={(e) => { e.stopPropagation(); onRetryInstall(engine); }}
            >
              {t('avatar.codingEngine.retryInstall', '重试安装')}
            </button>
          )}
        </span>
      );
    }
    
    return null;
  };

  return (
    <div className="coding-engine-select">
      <span className="coding-engine-select__label">{t('avatar.codingEngine.label', '编码能力')}</span>
      <p className="coding-engine-select__hint">{t('avatar.codingEngine.hint', '选择分身执行代码相关任务时使用的编码后端')}</p>
      <div className="coding-engine-select__options">
        {options.map((engine) => {
          const meta = ENGINE_META[engine];
          const selected = value === engine;
          const status = engineStatus?.[engine];
          const disabled = Boolean(status && !status.selectable);
          const cliStatus = status?.cli_install_status;
          
          return (
            <button
              key={engine}
              type="button"
              className={`coding-engine-select__option${selected ? ' coding-engine-select__option--selected' : ''}${disabled ? ' coding-engine-select__option--disabled' : ''}`}
              disabled={disabled}
              onClick={() => { if (!disabled) onChange(engine); }}
            >
              <span className="coding-engine-select__option-name">{t(meta.nameKey)}</span>
              <span className="coding-engine-select__option-desc">{t(meta.descKey)}</span>
              {disabled && status?.reason && (
                <span className="coding-engine-select__option-lock">{reasonText(status.reason)}</span>
              )}
              {renderCliInstallStatus(engine, cliStatus)}
            </button>
          );
        })}
      </div>
      <p className="coding-engine-select__config-hint">
        {onGoToConfig ? (
          <>
            {t('avatar.codingEngine.configHintPrefix', 'Claude Code / Codex 需先在')}
            <button
              type="button"
              className="coding-engine-select__config-link"
              onClick={onGoToConfig}
            >
              {t('avatar.codingEngine.configPage', '「配置」页')}
            </button>
            {t('avatar.codingEngine.configHintSuffix', '填写对应 API Key。')}
          </>
        ) : (
          t('avatar.codingEngine.configHint', 'Claude Code / Codex 需先在「配置」页填写对应 API Key。')
        )}
      </p>
    </div>
  );
}

export function codingEngineLabel(engine: string | null | undefined, t: (key: string) => string): string {
  if (!engine) return t('avatar.codingEngine.unset');
  const meta = ENGINE_META[engine as CodingEngine];
  return meta ? t(meta.nameKey) : engine;
}
