const DEFAULT_PRODUCT_NAME = 'JiuwenSwarm';

function parseEnvBool(raw: string | undefined): boolean {
  if (!raw) return false;
  const value = raw.trim().toLowerCase();
  return value === 'true' || value === '1' || value === 'yes';
}

/** 产品展示名称；读仓库根 `.env` 的 VITE_PRODUCT_NAME（Vite 构建/开发时注入）。 */
export function getProductName(): string {
  const raw = import.meta.env.VITE_PRODUCT_NAME;
  if (typeof raw === 'string' && raw.trim()) {
    return raw.trim();
  }
  return DEFAULT_PRODUCT_NAME;
}

/** 是否显示「本地一键拉起」；仅本地开发通过 Vite 读取 MANAGER_ALLOW_LOCAL_PROVISION。 */
export function getAllowLocalProvision(): boolean {
  return parseEnvBool(import.meta.env.VITE_MANAGER_ALLOW_LOCAL_PROVISION);
}
