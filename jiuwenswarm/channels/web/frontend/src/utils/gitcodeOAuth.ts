/**
 * GitCode OAuth 工具函数
 *
 * 提供 OAuth 登录 URL 构建、回调处理、token/用户信息存取等功能。
 * 被 App.tsx（回调处理）和 SkillPanel（登录弹窗）共用。
 *
 * 配置方式：在 frontend 目录下创建 .env.local 文件，设置：
 *   VITE_GITCODE_OAUTH_CLIENT_ID=你的Client ID
 *   VITE_GITCODE_OAUTH_CLIENT_SECRET=你的Client Secret
 */

// ── sessionStorage keys ──
const TOKEN_KEY = 'marketplace_oauth_access_token';
const PROVIDER_KEY = 'marketplace_oauth_provider';
const USER_KEY = 'marketplace_oauth_user';

// ── GitCode OAuth App 配置（从环境变量读取） ──
const GITCODE_OAUTH_CLIENT_ID = import.meta.env.VITE_GITCODE_OAUTH_CLIENT_ID || '';
const GITCODE_OAUTH_CLIENT_SECRET = import.meta.env.VITE_GITCODE_OAUTH_CLIENT_SECRET || '';

// ── 类型 ──
export type OAuthUser = {
  id: string;
  name: string;
  login: string;
  avatar_url: string;
  is_market_moderation_admin: boolean;
};

// ── 配置检查 ──
/** 检查 OAuth 是否已配置（Client ID 和 Client Secret 均已设置） */
export function isOAuthConfigured(): boolean {
  return !!(GITCODE_OAUTH_CLIENT_ID && GITCODE_OAUTH_CLIENT_SECRET);
}

// ── 存取函数 ──
export function getStoredOAuthToken(): string | null {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}

export function getStoredOAuthProvider(): string {
  try { return sessionStorage.getItem(PROVIDER_KEY) || 'gitcode'; } catch { return 'gitcode'; }
}

export function getStoredOAuthUser(): OAuthUser | null {
  try {
    const raw = sessionStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

// ── 构建 GitCode OAuth 登录 URL ──
export function buildGitCodeOAuthUrl(): string {
  if (!isOAuthConfigured()) {
    throw new Error('GitCode OAuth 未配置，请在 .env.local 中设置 VITE_GITCODE_OAUTH_CLIENT_ID 和 VITE_GITCODE_OAUTH_CLIENT_SECRET');
  }
  const redirectUri = window.location.origin + '/oauth/callback';
  const state = Math.random().toString(36).slice(2) + Date.now().toString(36);
  return `https://gitcode.com/oauth/authorize?client_id=${GITCODE_OAUTH_CLIENT_ID}&redirect_uri=${encodeURIComponent(redirectUri)}&response_type=code&scope=user_info&state=${state}`;
}

// ── 处理 OAuth 回调（页面加载时调用） ──
// 检测 URL 中的 code 参数，用 code + client_secret 换 access_token，再获取用户信息。
// 返回 true 表示处理了回调（无论成功或失败），false 表示 URL 中没有 code。
export async function processOAuthCallback(): Promise<boolean> {
  const params = new URLSearchParams(window.location.search);
  const code = params.get('code');
  const error = params.get('error');

  if (error) {
    // 清理 URL
    window.history.replaceState({}, '', window.location.pathname);
    return true;
  }

  if (!code) return false;

  if (!isOAuthConfigured()) {
    sessionStorage.setItem('oauth_error', 'GitCode OAuth 未配置，请检查 .env.local 中的 VITE_GITCODE_OAUTH_CLIENT_ID 和 VITE_GITCODE_OAUTH_CLIENT_SECRET');
    window.history.replaceState({}, '', window.location.pathname);
    return true;
  }

  // 用 code 换 access_token
  const tokenForm = new URLSearchParams();
  tokenForm.append('client_id', GITCODE_OAUTH_CLIENT_ID);
  tokenForm.append('client_secret', GITCODE_OAUTH_CLIENT_SECRET);
  tokenForm.append('code', code);
  tokenForm.append('grant_type', 'authorization_code');
  tokenForm.append('redirect_uri', window.location.origin + '/oauth/callback');

  const tokenResp = await fetch('/gitcode-api/oauth/token', {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body: tokenForm,
  });

  if (!tokenResp.ok) {
    sessionStorage.setItem('oauth_error', 'GitCode OAuth 认证失败：Client ID 或 Client Secret 不正确，请检查 .env.local 配置');
    window.history.replaceState({}, '', window.location.pathname);
    return true;
  }
  const tokenData = await tokenResp.json();
  if (!tokenData?.access_token) {
    sessionStorage.setItem('oauth_error', 'GitCode OAuth 认证失败：未返回 access_token，请检查 Client Secret 是否正确');
    window.history.replaceState({}, '', window.location.pathname);
    return true;
  }

  const accessToken = tokenData.access_token;
  sessionStorage.setItem(TOKEN_KEY, accessToken);
  sessionStorage.setItem(PROVIDER_KEY, 'gitcode');

  // 用 access_token 获取用户信息
  try {
    const userResp = await fetch(`/gitcode-api/api/v5/user?access_token=${encodeURIComponent(accessToken)}`, {
      headers: { Accept: 'application/json' },
    });
    if (userResp.ok) {
      const userData = await userResp.json();
      const login = userData.login || userData.username || '';
      const user: OAuthUser = {
        id: String(userData.id || ''),
        name: userData.name || login || '',
        login,
        avatar_url: userData.avatar_url || userData.avatar || '',
        is_market_moderation_admin: false,
      };
      sessionStorage.setItem(USER_KEY, JSON.stringify(user));
    }
  } catch {
    // 用户信息获取失败不影响 token 存储
  }

  // 清理 URL 中的 code 参数
  window.history.replaceState({}, '', window.location.pathname);
  return true;
}
