const DEFAULT_PRODUCT_NAME = 'JiuwenSwarm';

/** 产品展示名称；读 VITE_PRODUCT_NAME（Vite 构建/开发时注入），缺省 JiuwenSwarm。 */
export function getProductName(): string {
  const raw = import.meta.env.VITE_PRODUCT_NAME;
  if (typeof raw === 'string' && raw.trim()) {
    return raw.trim();
  }
  return DEFAULT_PRODUCT_NAME;
}
