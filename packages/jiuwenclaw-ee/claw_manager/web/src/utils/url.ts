/** 与后端一致：须为 http(s) 且含主机（对应 urlparse 的 scheme + netloc）。 */
export function isValidHttpUrl(value: string): boolean {
  const trimmed = value.trim();
  if (!trimmed) return false;
  try {
    const url = new URL(trimmed);
    return (
      (url.protocol === 'http:' || url.protocol === 'https:') && Boolean(url.host)
    );
  } catch {
    return false;
  }
}
