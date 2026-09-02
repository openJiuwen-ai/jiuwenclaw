import { type FormEvent, useState } from 'react';
import { EnterpriseAuthError, type EnterpriseAuthProvider } from '../types';

export function isUserWebAuthPath(pathname: string): boolean {
  return pathname === '/auth' || pathname === '/auth/';
}

export function UserWebLoginPage({
  provider,
  onLoggedIn,
}: {
  provider: EnterpriseAuthProvider;
  onLoggedIn: () => void;
}) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    setError('');
    setSubmitting(true);
    try {
      await provider.login(username.trim(), password);
      onLoggedIn();
    } catch (err) {
      setError(err instanceof EnterpriseAuthError && err.message ? err.message : '登录失败，请检查账号密码');
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="enterprise-entry">
      <div className="enterprise-entry__glow" />
      <form className="enterprise-entry__card" onSubmit={onSubmit}>
        <div className="enterprise-entry__brand">
          JIUWEN<span>CLAW</span>
        </div>
        <div className="enterprise-entry__eyebrow">ENTERPRISE WORKSPACE</div>
        <h1>用户登录</h1>
        <p>使用企业账号登录用户工作台。组织和 Agent 仍由管理员在 Manager 中授权下发。</p>
        <label htmlFor="user-web-username">用户名</label>
        <input
          id="user-web-username"
          className="enterprise-entry__input"
          autoFocus
          autoComplete="username"
          value={username}
          onChange={event => setUsername(event.target.value)}
        />
        <label htmlFor="user-web-password">密码</label>
        <input
          id="user-web-password"
          className="enterprise-entry__input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={event => setPassword(event.target.value)}
        />
        {error && <div className="enterprise-entry__error">{error}</div>}
        <button
          type="submit"
          className="enterprise-entry__button"
          disabled={submitting || !username.trim() || !password}
        >
          {submitting ? '正在登录…' : '登录'}
        </button>
      </form>
    </div>
  );
}
