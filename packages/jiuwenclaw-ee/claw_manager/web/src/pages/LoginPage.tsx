import { FormEvent, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useAuth } from '../auth/AuthContext';
import { ApiError } from '../services/api';

export function LoginPage() {
  const { t } = useTranslation();
  const { login } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await login(username.trim(), password);
    } catch (err) {
      // 后端用稳定 code(auth_bad_credentials / auth_disabled)→ 映射到双语文案;
      // 未知错误回退到通用句子,保证始终是规整句子。
      const codeMsg: Record<string, string> = {
        auth_bad_credentials: t('auth.errBadCredentials'),
        auth_disabled: t('auth.errDisabled'),
      };
      const detail = err instanceof ApiError ? err.detail : '';
      setError(codeMsg[detail] ?? t('auth.loginFailed'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="flex items-center justify-center h-screen p-8">
      <form className="card" style={{ width: 360 }} onSubmit={onSubmit}>
        <div className="flex items-center gap-2 mb-4">
          <img src="/logo.png" alt="JiuwenClaw" style={{ height: 28 }} />
          <div className="card-title">{t('auth.loginTitle')}</div>
        </div>

        <label className="label" htmlFor="login-username">{t('auth.username')}</label>
        <input
          id="login-username"
          className="input"
          autoFocus
          autoComplete="username"
          value={username}
          onChange={(e) => setUsername(e.target.value)}
        />

        <label className="label mt-3" htmlFor="login-password">{t('auth.password')}</label>
        <input
          id="login-password"
          className="input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />

        {error && <div className="text-danger text-sm mt-3">{error}</div>}

        <button className="btn primary mt-4" type="submit" disabled={submitting || !username || !password} style={{ width: '100%' }}>
          {submitting ? t('auth.loggingIn') : t('auth.login')}
        </button>
      </form>
    </div>
  );
}
