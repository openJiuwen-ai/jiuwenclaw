import { makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

type RpcPayload = {
  success?: boolean;
  detail?: string;
  content?: string;
  markdown?: string;
  exists?: boolean;
  stale?: boolean;
  skill_count?: number;
  extracted_count?: number;
  reused_count?: number;
  edge_count?: number;
};

function renderPayload(payload: RpcPayload): string {
  if (payload.content || payload.markdown) return payload.content || payload.markdown || "";
  return JSON.stringify(payload, null, 2);
}

async function runPlan(ctx: import("../types.js").CommandContext, query: string): Promise<void> {
  if (!query.trim()) {
    ctx.addItem(makeItem(ctx.sessionId, "error", "Usage: /symphony plan <task>"));
    return;
  }
  const payload = await ctx.request<RpcPayload>(
    "symphony.plan",
    { query },
    180_000,
  );
  ctx.addItem(
    makeItem(
      ctx.sessionId,
      payload.success === false ? "error" : "info",
      payload.success === false ? payload.detail || "Symphony planning failed." : renderPayload(payload),
      "*",
    ),
  );
}

export function createSymphonyCommand(): SlashCommand {
  return {
    name: "symphony",
    description: "Plan explicit execution across installed skills",
    usage: "/symphony [status|update|graph|plan] <task>",
    example: "/symphony plan 翻译图片中的英文并写成文章",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const trimmed = args.trim();
      if (!trimmed) {
        ctx.addItem(makeItem(ctx.sessionId, "error", "Usage: /symphony plan <task>"));
        return;
      }
      const [command, ...rest] = trimmed.split(/\s+/);
      const tail = rest.join(" ").trim();
      switch (command.toLowerCase()) {
        case "status": {
          const payload = await ctx.request<RpcPayload>("symphony.score_status", {}, 60_000);
          ctx.addItem(makeItem(ctx.sessionId, payload.success === false ? "error" : "info", renderPayload(payload), "*"));
          return;
        }
        case "update": {
          const payload = await ctx.request<RpcPayload>("symphony.build_score", {}, 180_000);
          ctx.addItem(makeItem(ctx.sessionId, payload.success === false ? "error" : "info", renderPayload(payload), "*"));
          return;
        }
        case "build": {
          const payload = await ctx.request<RpcPayload>("symphony.build_score", {}, 180_000);
          ctx.addItem(makeItem(ctx.sessionId, payload.success === false ? "error" : "info", renderPayload(payload), "*"));
          return;
        }
        case "graph": {
          const payload = await ctx.request<RpcPayload>("symphony.graph", {}, 60_000);
          ctx.addItem(makeItem(ctx.sessionId, payload.success === false ? "error" : "info", renderPayload(payload), "*"));
          return;
        }
        case "plan":
          await runPlan(ctx, tail);
          return;
        default:
          await runPlan(ctx, trimmed);
      }
    },
  };
}
