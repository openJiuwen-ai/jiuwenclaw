/** 与后端 safe_text 一致：拦截 XSS / 典型 SQL 注入恶意片段。 */

const CONTROL_RE = /[\x00-\x08\x0b\x0c\x0e-\x1f]/;
const HTML_TAG_RE = /<[^>]*>/;
const SCRIPT_URI_RE = /\b(javascript|vbscript|data\s*:\s*text\s*\/\s*html)\s*:/i;
const EVENT_HANDLER_RE = /\bon[a-z]+\s*=/i;
const SQLI_RE =
  /('\s*(or|and)\s+('?\d+'?\s*=\s*'?\d+'?|true|false)|;\s*(drop|delete|update|insert|alter|truncate|exec|execute|create)\b|\bunion\b\s+(all\s+)?\bselect\b|\/\*|\*\/|('|;)(\s)*--|\bxp_cmdshell\b|\binformation_schema\b)/i;

export function isSafeText(value: string): boolean {
  if (CONTROL_RE.test(value)) return false;
  if (value.includes('<') || value.includes('>') || HTML_TAG_RE.test(value)) return false;
  if (SCRIPT_URI_RE.test(value) || EVENT_HANDLER_RE.test(value)) return false;
  if (SQLI_RE.test(value)) return false;
  return true;
}

/** 返回首个不安全字段的 label；全部安全则返回 null。空字符串跳过。 */
export function findUnsafeTextField(
  fields: ReadonlyArray<{ label: string; value?: string | null }>,
): string | null {
  for (const field of fields) {
    const value = field.value?.trim();
    if (!value) continue;
    if (!isSafeText(value)) return field.label;
  }
  return null;
}
