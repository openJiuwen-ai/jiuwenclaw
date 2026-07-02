/**
 * 环境变量工具
 *
 * 开发（npm run dev）与部署均通过 Vite 构建时注入 `import.meta.env`；
 * 本地读仓库根 `.env`，镜像构建读 Dockerfile ARG `VITE_PRODUCT_NAME`。
 */
function normalizeBase(input: string): string {
  return input.replace(/\/+$/, '');
}

const DEFAULT_PRODUCT_NAME = 'JiuwenSwarm';

/** 产品展示名称；读 VITE_PRODUCT_NAME（Vite 构建/开发时注入）。 */
export function getProductName(): string {
  const raw = import.meta.env.VITE_PRODUCT_NAME;
  if (typeof raw === 'string' && raw.trim()) {
    return raw.trim();
  }
  return DEFAULT_PRODUCT_NAME;
}

export function getApiBase(): string {
  const raw = import.meta.env.VITE_API_BASE as string | undefined;
  if (!raw) return '';
  return normalizeBase(raw);
}

export function getWsBase(): string {
  const raw = import.meta.env.VITE_WS_BASE as string | undefined;
  if (raw) return normalizeBase(raw);
  const apiBase = getApiBase();
  if (!apiBase) return '';
  return apiBase.replace(/^http:/, 'ws:').replace(/^https:/, 'wss:');
}
