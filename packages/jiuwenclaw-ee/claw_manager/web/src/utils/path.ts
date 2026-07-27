/** 与后端一致：绝对 Unix 路径（以 / 开头，禁止 \、空段、.、..）。 */
export function isValidUnixAbsPath(value: string): boolean {
  const text = value.trim();
  if (!text || text.length > 512) return false;
  if (text.includes('\0') || text.includes('\\')) return false;
  if (!text.startsWith('/')) return false;
  if (text === '/') return true;
  const core = text.replace(/\/+$/, '');
  if (!core.startsWith('/')) return false;
  for (const segment of core.slice(1).split('/')) {
    if (!segment || segment === '.' || segment === '..') return false;
  }
  return true;
}

/** 可选路径：空视为未填；有值时须为合法绝对路径。 */
export function isValidOptionalUnixAbsPath(value: string): boolean {
  if (!value.trim()) return true;
  return isValidUnixAbsPath(value);
}
