import { useCallback, useEffect, useState } from 'react';
import { webRequest } from '../../services/webClient';
import './EnterprisePanel.css';

export interface ManagerStatus {
  enterprise_mode?: boolean;
  deployment_mode?: string;
  agent_server_deploy_mode?: string;
  store_backend?: string;
  session_map_backend?: string;
  runtime?: Record<string, unknown>;
}

export interface LocalUser {
  sub: string;
  group_id: string;
  display_name: string;
  role?: 'org_admin' | 'member' | 'admin' | 'platform_admin';
}

interface EnterprisePanelProps {
  status?: ManagerStatus | null;
  user?: LocalUser | null;
  mode?: 'login' | 'dashboard';
  onLogin?: (user: LocalUser) => void;
  onNavigateToAvatars?: () => void;
}

export function readEnterpriseUser(): LocalUser | null {
  try {
    const raw = localStorage.getItem('jiuwenavatar_enterprise_user');
    return raw ? JSON.parse(raw) as LocalUser : null;
  } catch {
    return null;
  }
}

export function writeEnterpriseUser(user: LocalUser | null): void {
  try {
    if (user) {
      localStorage.setItem('jiuwenavatar_enterprise_user', JSON.stringify(user));
    } else {
      localStorage.removeItem('jiuwenavatar_enterprise_user');
    }
  } catch {
    // ignore storage failures
  }
}

export function EnterprisePanel({
  status: providedStatus,
  user: providedUser,
  mode = 'dashboard',
  onLogin,
  onNavigateToAvatars,
}: EnterprisePanelProps) {
  const [status, setStatus] = useState<ManagerStatus | null>(providedStatus ?? null);
  const [user, setUser] = useState<LocalUser | null>(providedUser ?? readEnterpriseUser());
  const [userId, setUserId] = useState(user?.sub || '');
  const [groupId, setGroupId] = useState(user?.group_id || '');
  const [role, setRole] = useState<LocalUser['role']>(user?.role || 'member');
  const [error, setError] = useState('');

  useEffect(() => {
    if (providedStatus !== undefined) {
      setStatus(providedStatus);
    }
  }, [providedStatus]);

  useEffect(() => {
    if (providedUser !== undefined) {
      setUser(providedUser);
      setUserId(providedUser?.sub || '');
      setGroupId(providedUser?.group_id || '');
      setRole(providedUser?.role || 'member');
    }
  }, [providedUser]);

  const refreshStatus = useCallback(async () => {
    try {
      setError('');
      const result = await webRequest('manager.status') as ManagerStatus;
      setStatus(result);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void refreshStatus();
  }, [refreshStatus]);

  const login = useCallback(async () => {
    try {
      setError('');
      const nextUserId = userId.trim();
      const nextGroupId = groupId.trim();
      if (!nextUserId || !nextGroupId) {
        setError('请输入用户 ID 和租户/组织 ID');
        return;
      }
      const result = await webRequest('auth.login', {
        user_id: nextUserId,
        group_id: nextGroupId,
        display_name: nextUserId,
        role: role || 'member',
      }) as { user?: LocalUser };
      if (result.user) {
        setUser(result.user);
        writeEnterpriseUser(result.user);
        onLogin?.(result.user);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, [groupId, userId, role, onLogin]);

  const isLoginMode = mode === 'login';

  return (
    <div className={isLoginMode ? 'enterprise-login' : 'enterprise-dashboard'}>
      <section className="enterprise-hero">
        <div className="enterprise-hero__eyebrow">JiuwenAvatar Cloud</div>
        <h1>{isLoginMode ? '登录企业管理台' : '企业管理台'}</h1>
        <p>
          {isLoginMode
            ? '云化部署入口会先建立租户和用户上下文，后续分身、任务和报告都会沿用这个隔离身份。'
            : '查看分布式运行时状态，管理当前租户下的数字分身实例。'}
        </p>
      </section>

      <div className="enterprise-layout">
        <section className="enterprise-card enterprise-card--login">
          <div className="enterprise-card__header">
            <div>
              <h2>租户登录</h2>
              <p>当前阶段是本地 Identity shim，后续替换为正式 OAuth2 / JWT。</p>
            </div>
          </div>

          {error && <div className="enterprise-alert">{error}</div>}

          <label className="enterprise-field">
            <span>用户 ID</span>
            <input
              value={userId}
              placeholder="例如 user-001"
              onChange={(event) => setUserId(event.target.value)}
            />
          </label>
          <label className="enterprise-field">
            <span>租户 / 组织 ID</span>
            <input
              value={groupId}
              placeholder="例如 group-default"
              onChange={(event) => setGroupId(event.target.value)}
            />
          </label>
          <label className="enterprise-field">
            <span>角色</span>
            <select
              value={role || 'member'}
              onChange={(event) => setRole(event.target.value as LocalUser['role'])}
            >
              <option value="member">普通成员（仅选模型）</option>
              <option value="org_admin">租户管理员（配置模型目录）</option>
            </select>
          </label>

          <button type="button" className="enterprise-primary" onClick={login}>
            登录并进入控制台
          </button>

          {user && (
            <div className="enterprise-current-user">
              当前上下文：{user.display_name} / {user.group_id}
            </div>
          )}
        </section>

        <section className="enterprise-card">
          <div className="enterprise-card__header">
            <div>
              <h2>运行时状态</h2>
              <p>由 Gateway 返回，单机模式不会进入该入口。</p>
            </div>
            {!providedStatus && (
              <button type="button" className="enterprise-secondary" onClick={refreshStatus}>
                刷新
              </button>
            )}
          </div>
          <div className="enterprise-status-grid">
            <span>企业模式</span><strong>{status?.enterprise_mode ? '已启用' : '未启用'}</strong>
            <span>部署模式</span><strong>{status?.deployment_mode || 'standalone'}</strong>
            <span>AgentServer</span><strong>{status?.agent_server_deploy_mode || 'process'}</strong>
            <span>存储后端</span><strong>{status?.store_backend || 'json'}</strong>
            <span>SessionMap</span><strong>{status?.session_map_backend || 'auto'}</strong>
          </div>
        </section>

        {!isLoginMode && (
          <section className="enterprise-card enterprise-card--wide">
            <h2>数字分身实例</h2>
            <p>登录后进入“分身”页面创建和管理当前租户下的数字分身。创建请求会自动携带当前用户和租户上下文。</p>
            <button type="button" className="enterprise-secondary" onClick={onNavigateToAvatars}>
              打开我的分身
            </button>
          </section>
        )}
      </div>
    </div>
  );
}
