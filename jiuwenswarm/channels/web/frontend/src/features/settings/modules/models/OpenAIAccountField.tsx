import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Copy, ExternalLink, KeyRound, LogOut, RefreshCw } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { ModelEntry } from '../../../../types';
import { Button, Loading, Select } from '../../../../components/ui';
import { OPENAI_ACCOUNT_RPC, type SettingsRequest } from '../../services/settingsContract';
import './OpenAIAccountField.css';

function SettingsBadge({
  children,
  tone = 'neutral',
}: {
  children: ReactNode;
  tone?: 'neutral' | 'success' | 'warning';
}) {
  return <span className={`settings-oauth__badge settings-oauth__badge--${tone}`}>{children}</span>;
}

const SettingsButton = Button;

const DEFAULT_API_BASE = 'https://chatgpt.com/backend-api/codex';
const LOGIN_POLL_MINIMUM_MS = 15_000;
const AUTH_REQUEST_TIMEOUT_MS = 45_000;
const MODEL_REQUEST_TIMEOUT_MS = 75_000;
const LOGIN_START_TIMEOUT_MS = 90_000;

type AuthStatus = {
  authenticated: boolean;
  auth_path?: string;
  needs_refresh?: boolean;
  base_url?: string;
};

type LoginPayload = {
  status: 'pending';
  login_id: string;
  user_code: string;
  verification_uri: string;
  interval: number;
  auth?: AuthStatus;
};

type PendingLoginPayload =
  | LoginPayload
  | {
      status: 'none';
      auth?: AuthStatus;
    };

type PollPayload = {
  status: 'pending' | 'authenticated' | 'expired' | 'error';
  auth?: AuthStatus;
  error?: string;
};

type ModelsPayload = {
  models?: string[];
  base_url?: string;
  auth?: AuthStatus;
};

function errorMessage(error: unknown, fallback: string): string {
  return error instanceof Error && error.message ? error.message : fallback;
}

function isRetriable(error: unknown): boolean {
  return error instanceof Error && (error as Error & { retriable?: boolean }).retriable === true;
}

export function OpenAIAccountSettings({
  model,
  connected,
  disabled,
  request,
  onModelPatch,
  onBlockingChange,
}: {
  model: ModelEntry;
  connected: boolean;
  disabled: boolean;
  request: SettingsRequest;
  onModelPatch: (patch: Partial<ModelEntry>) => void;
  onBlockingChange: (blocking: boolean) => void;
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<AuthStatus | null>(null);
  const [login, setLogin] = useState<LoginPayload | null>(null);
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [loadingStatus, setLoadingStatus] = useState(true);
  const [loadingModels, setLoadingModels] = useState(false);
  const [startingLogin, setStartingLogin] = useState(false);
  const [pollingLogin, setPollingLogin] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const modelRef = useRef(model);
  const statusRef = useRef<AuthStatus | null>(null);
  const onModelPatchRef = useRef(onModelPatch);

  useEffect(() => {
    modelRef.current = model;
  }, [model]);

  useEffect(() => {
    statusRef.current = status;
  }, [status]);

  useEffect(() => {
    onModelPatchRef.current = onModelPatch;
  }, [onModelPatch]);

  const applyProviderDefaults = useCallback((modelIds: string[] = [], baseUrl?: string) => {
    const currentModelName = modelRef.current.model_name.trim();
    onModelPatchRef.current({
      model_provider: 'OpenAIAccount',
      api_base: baseUrl || statusRef.current?.base_url || DEFAULT_API_BASE,
      api_key: '',
      model_name: currentModelName || modelIds[0] || '',
    });
  }, []);

  const loadModels = useCallback(
    async (baseUrl?: string) => {
      setLoadingModels(true);
      setError(null);
      try {
        const payload = await request<ModelsPayload>(
          OPENAI_ACCOUNT_RPC.listModels,
          {},
          { timeoutMs: MODEL_REQUEST_TIMEOUT_MS },
        );
        const nextModels = Array.from(
          new Set(
            (payload.models ?? [])
              .filter((name): name is string => typeof name === 'string')
              .map((name) => name.trim())
              .filter(Boolean),
          ),
        );
        setModelOptions(nextModels);
        if (payload.auth) setStatus(payload.auth);
        applyProviderDefaults(nextModels, payload.base_url || baseUrl);
        if (nextModels.length === 0) setError(t('config.openaiAccount.noModelsAvailable'));
      } catch (loadError) {
        setError(errorMessage(loadError, t('config.openaiAccount.modelsLoadFailed')));
      } finally {
        setLoadingModels(false);
      }
    },
    [applyProviderDefaults, request, t],
  );

  const completeAuthentication = useCallback(
    async (nextStatus?: AuthStatus) => {
      const resolvedStatus = nextStatus ?? statusRef.current;
      if (resolvedStatus) setStatus(resolvedStatus);
      setLogin(null);
      await loadModels(resolvedStatus?.base_url);
    },
    [loadModels],
  );

  const pollLoginOnce = useCallback(
    async (activeLogin: LoginPayload): Promise<boolean> => {
      if (!connected) return true;
      setPollingLogin(true);
      setError(null);
      try {
        const payload = await request<PollPayload>(
          OPENAI_ACCOUNT_RPC.pollLogin,
          { login_id: activeLogin.login_id },
          { timeoutMs: AUTH_REQUEST_TIMEOUT_MS },
        );
        if (payload.status === 'authenticated' || payload.auth?.authenticated) {
          await completeAuthentication(payload.auth);
          return true;
        }
        if (payload.status === 'expired') {
          setLogin(null);
          setError(t('config.openaiAccount.loginExpired'));
          return true;
        }
        if (payload.status === 'error') {
          setLogin(null);
          setError(payload.error || t('config.openaiAccount.loginFailed'));
          return true;
        }
        return false;
      } catch (pollError) {
        setError(errorMessage(pollError, t('config.openaiAccount.loginFailed')));
        if (isRetriable(pollError)) return false;
        setLogin(null);
        return true;
      } finally {
        setPollingLogin(false);
      }
    },
    [completeAuthentication, connected, request, t],
  );

  const restoreAuth = useCallback(async () => {
    if (!connected) {
      setLoadingStatus(false);
      return;
    }
    setLoadingStatus(true);
    setError(null);
    try {
      const payload = await request<PendingLoginPayload>(
        OPENAI_ACCOUNT_RPC.pendingLogin,
        {},
        { timeoutMs: AUTH_REQUEST_TIMEOUT_MS },
      );
      const nextStatus = payload.auth ?? null;
      setStatus(nextStatus);
      if (payload.status === 'pending') {
        setLogin(payload);
      } else if (nextStatus?.authenticated) {
        await loadModels(nextStatus.base_url);
      }
    } catch (statusError) {
      setError(errorMessage(statusError, t('config.openaiAccount.statusFailed')));
    } finally {
      setLoadingStatus(false);
    }
  }, [connected, loadModels, request, t]);

  useEffect(() => {
    void restoreAuth();
  }, [restoreAuth]);

  useEffect(() => {
    if (!login || !connected) return undefined;
    let cancelled = false;
    let timer: number | undefined;
    const delay = Math.max(LOGIN_POLL_MINIMUM_MS, login.interval * 1000);
    const run = async () => {
      const finished = await pollLoginOnce(login);
      if (!cancelled && !finished) timer = window.setTimeout(run, delay);
    };
    timer = window.setTimeout(run, delay);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [connected, login, pollLoginOnce]);

  const refreshStatus = async () => {
    if (!connected) return;
    if (login) {
      await pollLoginOnce(login);
      return;
    }
    setLoadingStatus(true);
    setError(null);
    try {
      const nextStatus = await request<AuthStatus>(
        OPENAI_ACCOUNT_RPC.status,
        {},
        { timeoutMs: AUTH_REQUEST_TIMEOUT_MS },
      );
      setStatus(nextStatus);
      if (nextStatus.authenticated) await loadModels(nextStatus.base_url);
    } catch (statusError) {
      setError(errorMessage(statusError, t('config.openaiAccount.statusFailed')));
    } finally {
      setLoadingStatus(false);
    }
  };

  const startLogin = async () => {
    if (!connected) return;
    setStartingLogin(true);
    setError(null);
    setCopied(false);
    applyProviderDefaults();
    try {
      const payload = await request<LoginPayload>(
        OPENAI_ACCOUNT_RPC.startLogin,
        {},
        { timeoutMs: LOGIN_START_TIMEOUT_MS },
      );
      setLogin(payload);
      if (payload.auth) setStatus(payload.auth);
      window.open(payload.verification_uri, '_blank', 'noopener,noreferrer');
    } catch (loginError) {
      setError(errorMessage(loginError, t('config.openaiAccount.loginFailed')));
    } finally {
      setStartingLogin(false);
    }
  };

  const logout = async () => {
    if (!connected) return;
    setLoggingOut(true);
    setError(null);
    try {
      const payload = await request<{ auth?: AuthStatus }>(
        OPENAI_ACCOUNT_RPC.logout,
        {},
        { timeoutMs: AUTH_REQUEST_TIMEOUT_MS },
      );
      setStatus(payload.auth ?? null);
      setLogin(null);
      setModelOptions([]);
    } catch (logoutError) {
      setError(errorMessage(logoutError, t('config.openaiAccount.logoutFailed')));
    } finally {
      setLoggingOut(false);
    }
  };

  const copyCode = async () => {
    if (!login) return;
    try {
      await navigator.clipboard.writeText(login.user_code);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setError(t('config.openaiAccount.copyFailed'));
    }
  };

  const visibleModelOptions = useMemo(
    () => Array.from(new Set(modelOptions.map((name) => name.trim()).filter(Boolean))),
    [modelOptions],
  );
  const authenticated = Boolean(status?.authenticated && !status.needs_refresh);
  const selectedModel = visibleModelOptions.includes(model.model_name.trim()) ? model.model_name.trim() : '';
  const localBusy = loadingStatus || loadingModels || startingLogin || pollingLogin || loggingOut;
  const blocking = localBusy || Boolean(login);

  useEffect(() => {
    onBlockingChange(blocking);
    return () => onBlockingChange(false);
  }, [blocking, onBlockingChange]);

  return (
    <section className="settings-oauth" aria-label={t('config.openaiAccount.title')}>
      <div className="settings-oauth__header">
        <div className="settings-oauth__identity">
          <span className="settings-oauth__mark" aria-hidden>
            OpenAI
          </span>
          <div>
            <strong>{t('config.openaiAccount.title')}</strong>
            <span>
              {status?.auth_path
                ? t('config.openaiAccount.statusAuthPath', { path: status.auth_path })
                : t('config.openaiAccount.description')}
            </span>
          </div>
        </div>
        <div className="settings-oauth__actions">
          <SettingsBadge tone={authenticated ? 'success' : status?.needs_refresh ? 'warning' : 'neutral'}>
            {authenticated
              ? t('config.openaiAccount.connected')
              : status?.needs_refresh
                ? t('config.openaiAccount.refreshNeeded')
                : t('config.openaiAccount.notConnected')}
          </SettingsBadge>
          <SettingsButton
            variant="quiet"
            disabled={disabled || !connected || localBusy}
            onClick={() => void refreshStatus()}
          >
            {loadingStatus || pollingLogin ? <Loading size="sm" aria-label="" /> : <RefreshCw size={14} />}
            {t('config.openaiAccount.refresh')}
          </SettingsButton>
          {authenticated ? (
            <SettingsButton
              variant="quiet"
              disabled={disabled || !connected || localBusy}
              onClick={() => void logout()}
            >
              {loggingOut ? <Loading size="sm" aria-label="" /> : <LogOut size={14} />}
              {t('config.openaiAccount.logout')}
            </SettingsButton>
          ) : (
            <SettingsButton
              variant="primary"
              disabled={disabled || !connected || localBusy || Boolean(login)}
              onClick={() => void startLogin()}
            >
              {startingLogin ? <Loading size="sm" aria-label="" /> : <KeyRound size={14} />}
              {login ? t('config.openaiAccount.waitingAuth') : t('config.openaiAccount.connect')}
            </SettingsButton>
          )}
        </div>
      </div>

      <label className="settings-oauth__model-field">
        <span>{t('config.openaiAccount.modelSelectLabel')}</span>
        <Select
          aria-label={t('config.openaiAccount.modelSelectLabel')}
          value={selectedModel}
          disabled={disabled || !authenticated || loadingModels || visibleModelOptions.length === 0}
          onChange={(model_name) => onModelPatch({ model_name })}
          options={[
            ...(!selectedModel ? [{ value: '', label: t('config.openaiAccount.modelSelectPlaceholder') }] : []),
            ...visibleModelOptions.map((modelId) => ({ value: modelId, label: modelId })),
          ]}
        />
        <small>
          {loadingModels
            ? t('config.openaiAccount.loadingModels')
            : t('config.openaiAccount.modelsLoaded', { count: visibleModelOptions.length })}
        </small>
      </label>

      {login ? (
        <div className="settings-oauth__login">
          <div>
            <span>{t('config.openaiAccount.authCodeLabel')}</span>
            <strong>{login.user_code}</strong>
            <small>{t('config.openaiAccount.loginTimeHint')}</small>
          </div>
          <div className="settings-oauth__actions">
            <SettingsButton onClick={() => window.open(login.verification_uri, '_blank', 'noopener,noreferrer')}>
              <ExternalLink size={14} />
              {t('config.openaiAccount.openAuthPage')}
            </SettingsButton>
            <SettingsButton onClick={() => void copyCode()}>
              <Copy size={14} />
              {copied ? t('config.openaiAccount.copied') : t('config.openaiAccount.copyCode')}
            </SettingsButton>
          </div>
        </div>
      ) : null}

      {error ? (
        <div className="settings-oauth__error" role="alert">
          {error}
        </div>
      ) : null}
    </section>
  );
}
