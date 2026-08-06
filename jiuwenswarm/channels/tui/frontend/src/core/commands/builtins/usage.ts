import { addInfo, addError } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";
import type { ModelUsageEntry, NamedUsageEntry } from "../../../app-state.js";

/** Sub-cent sessions are normal; two decimals would render every one of them as $0.00. */
export function formatUsd(amount: number): string {
  if (!Number.isFinite(amount) || amount <= 0) return "$0.00";
  if (amount < 0.01) return `$${amount.toFixed(4)}`;
  return `$${amount.toFixed(2)}`;
}

/**
 * Per-bucket cost line. An understated bucket still shows its USD — hiding the
 * number behind "unpriced" made partial turns look like they cost nothing.
 */
export function formatModelCost(
  entry: Pick<ModelUsageEntry | NamedUsageEntry, "total_cost" | "unpriced">,
): string {
  if (!entry.unpriced) return formatUsd(entry.total_cost);
  if (entry.total_cost > 0) return `${formatUsd(entry.total_cost)} (understated)`;
  return "unpriced";
}

function pushBucketLines(
  items: { label: string; value: string }[],
  kind: "model" | "member" | "agent",
  name: string,
  entry: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    total_cost: number;
    unpriced: boolean;
    last_verified?: string;
  },
  fmt: (n: number) => string,
): void {
  items.push(
    { label: `${kind}: ${name}`, value: `${fmt(entry.total_tokens)} tokens` },
    { label: `  input`, value: fmt(entry.input_tokens) },
    { label: `  output`, value: fmt(entry.output_tokens) },
    { label: `  cost`, value: formatModelCost(entry) },
  );
  if (entry.last_verified) {
    items.push({ label: `  rates as of`, value: entry.last_verified });
  }
}

function showUsage(ctx: import("../types.js").CommandContext): void {
  const summary = ctx.getUsageSummary();
  const fmt = (n: number) => n.toLocaleString("en-US");

  const items = [
    { label: "input_tokens", value: fmt(summary.total_input_tokens) },
    { label: "output_tokens", value: fmt(summary.total_output_tokens) },
    { label: "total_tokens", value: fmt(summary.total_tokens) },
  ];

  // Never print a dollar amount for an unpriced session: a $0.00 that means
  // "no rate configured" is indistinguishable from one that means "free".
  if (summary.cost_status === "unpriced") {
    items.push({ label: "cost", value: "unpriced — set models.pricing in config.yaml" });
  } else {
    items.push({ label: "cost", value: formatUsd(summary.total_cost) });
    if (summary.cost_status === "partial") {
      const missing = [
        ...summary.byModel.filter((e) => e.unpriced).map((e) => e.model),
        ...summary.byMember.filter((e) => e.unpriced).map((e) => e.name),
        ...summary.byAgent.filter((e) => e.unpriced).map((e) => e.name),
      ];
      items.push({
        label: "  note",
        value: `partial — no rate for ${missing.join(", ")}, cost is understated`,
      });
    }
  }

  for (const entry of summary.byModel) {
    pushBucketLines(items, "model", entry.model, entry, fmt);
  }
  for (const entry of summary.byMember) {
    pushBucketLines(items, "member", entry.name, entry, fmt);
  }
  for (const entry of summary.byAgent) {
    pushBucketLines(items, "agent", entry.name, entry, fmt);
  }

  ctx.addItem(
    addInfo(ctx.sessionId, "Session usage", "u", {
      view: "kv",
      title: "Usage",
      items,
    }),
  );
}

export function createUsageCommand(): SlashCommand {
  return {
    name: "usage",
    description: "Show session token usage and cost (input / output / total / USD)",
    usage: "/usage",
    example: "/usage",
    kind: CommandKind.BUILT_IN,
    hidden: true,
    action: async (ctx) => {
      if (ctx.enterStatusView) {
        ctx.enterStatusView("usage");
        return;
      }
      try {
        showUsage(ctx);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `usage failed: ${message}`));
      }
    },
  };
}
