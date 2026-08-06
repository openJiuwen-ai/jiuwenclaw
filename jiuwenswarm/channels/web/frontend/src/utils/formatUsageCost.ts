/**
 * Format the USD line under an assistant message.
 *
 * Rules:
 * - Provider-only total (OpenRouter): "$X.XXXX total"
 * - Local split: round in/out to 4dp, then total = their sum (never toFixed each
 *   independently — that makes $0.0020+$0.0020≠$0.0039)
 * - If billed total disagrees with in+out, show billed total only
 */

export type UsageCostFields = {
  input_cost?: number;
  output_cost?: number;
  total_cost?: number;
  cost_status?: string;
};

/** Half of one 4-decimal display unit — beyond this, treat as real disagreement. */
const SPLIT_MISMATCH = 5e-5;

function money4(amount: number): string {
  return `$${amount.toFixed(4)}`;
}

export function formatUsageCostLine(usage: UsageCostFields): string | null {
  const total = usage.total_cost;
  if (total == null || !(total > 0) || !Number.isFinite(total)) {
    return null;
  }

  const inp = usage.input_cost ?? 0;
  const out = usage.output_cost ?? 0;
  const split = (Number.isFinite(inp) ? Math.max(0, inp) : 0)
    + (Number.isFinite(out) ? Math.max(0, out) : 0);
  const partial = usage.cost_status === 'partial' ? ' (partial)' : '';

  if (!(split > 0) || Math.abs(split - total) > SPLIT_MISMATCH) {
    return `${money4(total)} total${partial}`;
  }

  const i = Number(inp.toFixed(4));
  const o = Number(out.toFixed(4));
  const t = Number((i + o).toFixed(4));
  const parts: string[] = [];
  if (i > 0) parts.push(`${money4(i)} in`);
  if (o > 0) parts.push(`${money4(o)} out`);
  parts.push(`${money4(t)} total`);
  return parts.join(' / ') + partial;
}
