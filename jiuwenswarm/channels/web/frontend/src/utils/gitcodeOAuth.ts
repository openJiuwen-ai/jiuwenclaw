/**
 * OAuth 工具函数（支持 GitCode + GitHub 双 provider）
 *
 * 提供 OAuth 登录 URL 构建、回调处理、token/用户信息存取等功能。
 * 被 App.tsx（回调处理）和 SkillPanel（登录弹窗）共用。
 *
 * 配置方式：在 frontend 目录下创建 .env.local 文件，设置：
 *   VITE_GITCODE_OAUTH_CLIENT_ID=你的GitCode Client ID
 *   VITE_GITCODE_OAUTH_CLIENT_SECRET=你的GitCode Client Secret
 *   VITE_GITHUB_OAUTH_CLIENT_ID=你的GitHub Client ID
 *   VITE_GITHUB_OAUTH_CLIENT_SECRET=你的GitHub Client Secret
 *
 * Vite 代理：
 *   /gitcode-api/ → gitcode.com（token 兑换 + 用户信息）
 *   /github-oauth/ → github.com（token 兑换：POST /login/oauth/access_token）
 *   /github-api/ → api.github.com（用户信息：GET /user）
 */

// ── sessionStorage keys ──
const TOKEN_KEY = 'marketplace_oauth_access_token';
const PROVIDER_KEY = 'marketplace_oauth_provider';
const USER_KEY = 'marketplace_oauth_user';
// 临时记录当前正在进行的 OAuth provider（跳转前写、回调时读）
const ACTIVE_PROVIDER_KEY = 'marketplace_oauth_active_provider';

// ── GitCode OAuth App 配置（从环境变量读取） ──
const GITCODE_OAUTH_CLIENT_ID = import.meta.env.VITE_GITCODE_OAUTH_CLIENT_ID || '';
const GITCODE_OAUTH_CLIENT_SECRET = import.meta.env.VITE_GITCODE_OAUTH_CLIENT_SECRET || '';

// ── GitHub OAuth App 配置（从环境变量读取） ──
const GITHUB_OAUTH_CLIENT_ID = import.meta.env.VITE_GITHUB_OAUTH_CLIENT_ID || '';
const GITHUB_OAUTH_CLIENT_SECRET = import.meta.env.VITE_GITHUB_OAUTH_CLIENT_SECRET || '';

// ── 类型 ──
export type OAuthProvider = 'gitcode' | 'github';

export type OAuthUser = {
  id: string;
  name: string;
  login: string;
  avatar_url: string;
  is_market_moderation_admin: boolean;
};

// ── 配置检查 ──
/** 检查 GitCode OAuth 是否已配置 */
export function isOAuthConfigured(): boolean {
  return isGitCodeConfigured() || isGitHubConfigured();
}

export function isGitCodeConfigured(): boolean {
  return !!(GITCODE_OAUTH_CLIENT_ID && GITCODE_OAUTH_CLIENT_SECRET);
}

export function isGitHubConfigured(): boolean {
  return !!(GITHUB_OAUTH_CLIENT_ID && GITHUB_OAUTH_CLIENT_SECRET);
}

// ── 存取函数 ──
export function getStoredOAuthToken(): string | null {
  try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; }
}

export function getStoredOAuthProvider(): OAuthProvider {
  try {
    const p = (sessionStorage.getItem(PROVIDER_KEY) || '').trim().toLowerCase();
    return p === 'github' ? 'github' : 'gitcode';
  } catch { return 'gitcode'; }
}

export function getStoredOAuthUser(): OAuthUser | null {
  try {
    const raw = sessionStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

// ── 构建 OAuth 登录 URL ──
/** 构建 GitCode OAuth 登录 URL */
export function buildGitCodeOAuthUrl(): string {
  if (!isGitCodeConfigured()) {
    throw new Error('GitCode OAuth 未配置，请在 .env.local 中设置 VITE_GITCODE_OAUTH_CLIENT_ID 和 VITE_GITCODE_OAUTH_CLIENT_SECRET');
  }
  const redirectUri = window.location.origin + '/oauth/callback';
  const state = Math.random().toString(36).slice(2) + Date.now().toString(36);
  return `https://gitcode.com/oauth/authorize?client_id=${GITCODE_OAUTH_CLIENT_ID}&redirect_uri=${encodeURIComponent(redirectUri)}&response_type=code&scope=user_info&state=${state}`;
}

/** 构建 GitHub OAuth 登录 URL */
export function buildGitHubOAuthUrl(): string {
  if (!isGitHubConfigured()) {
    throw new Error('GitHub OAuth 未配置，请在 .env.local 中设置 VITE_GITHUB_OAUTH_CLIENT_ID 和 VITE_GITHUB_OAUTH_CLIENT_SECRET');
  }
  const redirectUri = window.location.origin + '/oauth/callback';
  const state = Math.random().toString(36).slice(2) + Date.now().toString(36);
  // scope=user 比 read:user 更轻量，足以获取用户身份和头像
  return `https://github.com/login/oauth/authorize?client_id=${GITHUB_OAUTH_CLIENT_ID}&redirect_uri=${encodeURIComponent(redirectUri)}&scope=user&state=${state}`;
}

/** 根据 provider 构建对应的 OAuth 登录 URL，并写入 active provider 标记 */
export function buildOAuthUrl(provider: OAuthProvider): string {
  sessionStorage.setItem(ACTIVE_PROVIDER_KEY, provider);
  return provider === 'github' ? buildGitHubOAuthUrl() : buildGitCodeOAuthUrl();
}

// ── 处理 OAuth 回调（页面加载时调用） ──
// 检测 URL 中的 code 参数，根据 active provider 走对应流程换 access_token + 获取用户信息。
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

  // 优先读 active provider 标记；缺省回退到已存储 provider；再缺省默认 gitcode
  const activeProvider = (sessionStorage.getItem(ACTIVE_PROVIDER_KEY) || getStoredOAuthProvider() || 'gitcode') as OAuthProvider;
  sessionStorage.removeItem(ACTIVE_PROVIDER_KEY);

  if (activeProvider === 'github') {
    return processGitHubCallback(code);
  }
  return processGitCodeCallback(code);
}

/** 处理 GitCode OAuth 回调：code → access_token → 用户信息 */
async function processGitCodeCallback(code: string): Promise<boolean> {
  if (!isGitCodeConfigured()) {
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

/** 处理 GitHub OAuth 回调：code → access_token → 用户信息 */
async function processGitHubCallback(code: string): Promise<boolean> {
  if (!isGitHubConfigured()) {
    sessionStorage.setItem('oauth_error', 'GitHub OAuth 未配置，请检查 .env.local 中的 VITE_GITHUB_OAUTH_CLIENT_ID 和 VITE_GITHUB_OAUTH_CLIENT_SECRET');
    window.history.replaceState({}, '', window.location.pathname);
    return true;
  }

  // 用 code 换 access_token：POST github.com/login/oauth/access_token
  const tokenForm = new URLSearchParams();
  tokenForm.append('client_id', GITHUB_OAUTH_CLIENT_ID);
  tokenForm.append('client_secret', GITHUB_OAUTH_CLIENT_SECRET);
  tokenForm.append('code', code);
  tokenForm.append('redirect_uri', window.location.origin + '/oauth/callback');

  const tokenResp = await fetch('/github-oauth/login/oauth/access_token', {
    method: 'POST',
    headers: { Accept: 'application/json' },
    body: tokenForm,
  });

  if (!tokenResp.ok) {
    sessionStorage.setItem('oauth_error', 'GitHub OAuth 认证失败：Client ID 或 Client Secret 不正确，请检查 .env.local 配置');
    window.history.replaceState({}, '', window.location.pathname);
    return true;
  }
  const tokenData = await tokenResp.json();
  if (!tokenData?.access_token) {
    sessionStorage.setItem('oauth_error', 'GitHub OAuth 认证失败：未返回 access_token，请检查 Client Secret 是否正确');
    window.history.replaceState({}, '', window.location.pathname);
    return true;
  }

  const accessToken = tokenData.access_token;
  sessionStorage.setItem(TOKEN_KEY, accessToken);
  sessionStorage.setItem(PROVIDER_KEY, 'github');

  // 用 access_token 获取用户信息：GET api.github.com/user
  try {
    const userResp = await fetch('/github-api/user', {
      headers: {
        Accept: 'application/json',
        Authorization: `Bearer ${accessToken}`,
      },
    });
    if (userResp.ok) {
      const userData = await userResp.json();
      const login = userData.login || '';
      const user: OAuthUser = {
        id: String(userData.id || ''),
        name: userData.name || login || '',
        login,
        avatar_url: userData.avatar_url || '',
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
