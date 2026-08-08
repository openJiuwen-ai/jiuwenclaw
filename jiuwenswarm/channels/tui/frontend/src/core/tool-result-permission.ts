const PERMISSION_DENIED_PREFIXES = ["[PERMISSION_DENIED]", "[PERMISSION_REJECTED]"] as const;

export function isPermissionDeniedToolResult(value: unknown): boolean {
  if (typeof value !== "string") {
    return false;
  }
  const trimmed = value.trimStart();
  return PERMISSION_DENIED_PREFIXES.some((prefix) => trimmed.startsWith(prefix));
}

function truncateFeedback(text: string, max = 72): string {
  const trimmed = text.trim();
  if (trimmed.length <= max) {
    return trimmed;
  }
  return `${trimmed.slice(0, max - 1)}…`;
}

export function summarizePermissionDeniedToolResult(result: string): string | undefined {
  if (!isPermissionDeniedToolResult(result)) {
    return undefined;
  }
  const feedbackMatch =
    result.match(/User feedback:\s*(.+)$/s) ?? result.match(/用户说明：(.+)$/s);
  const feedback = feedbackMatch?.[1]?.trim();
  if (feedback) {
    return `permission denied · ${truncateFeedback(feedback)}`;
  }
  return "permission denied";
}
