import { addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export function createPlanCommand(): SlashCommand {
  return {
    name: "plan",
    hidden: true, // TUI release: not registered in registry.ts; re-enable next version
    description: "Switch to agent plan mode, or send a planning request",
    usage: "/plan [open|<description>]",
    example: "/plan outline the migration steps",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: (ctx, args) => {
      if (ctx.mode === "team") {
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            "/plan does not apply in team mode; switch mode first (e.g. /mode agent).",
            "p",
          ),
        );
        return;
      }

      const value = args.trim();
      const target = "agent.plan";
      if (ctx.mode !== target) {
        ctx.setMode(target);
      }

      if (!value) {
        ctx.addItem(addInfo(ctx.sessionId, "Plan mode enabled", "p"));
        return;
      }

      if (value === "open") {
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            "Plan mode is active. Type your planning request directly or run /plan <description>.",
            "p",
          ),
        );
        return;
      }

      const requestId = ctx.sendMessage(value, undefined, target);
      if (!requestId) {
        ctx.addItem(
          addInfo(ctx.sessionId, "offline: waiting for reconnect before sending plan request", "p"),
        );
      }
    },
  };
}
