import type { ClientMode } from "../../modes.js";
import { isTeamMode } from "../../modes.js";
import { makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

type SwitchArg = "plan" | "fast" | "normal" | "team";

const AGENT_MODES = new Set<ClientMode>(["agent.plan", "agent.fast"]);
const CODE_MODES = new Set<ClientMode>(["code.normal", "code.team", "code.plan"]);

function resolveRequestedMode(currentMode: ClientMode, switchArg: SwitchArg): ClientMode | null {
  if (switchArg === "plan") {
    if (AGENT_MODES.has(currentMode)) return "agent.plan";
    if (CODE_MODES.has(currentMode)) return "code.plan";
    return null;
  }
  if (switchArg === "fast") {
    return AGENT_MODES.has(currentMode) ? "agent.fast" : null;
  }
  if (switchArg === "normal") {
    return CODE_MODES.has(currentMode) ? "code.normal" : null;
  }
  if (switchArg === "team") {
    return CODE_MODES.has(currentMode) ? "code.team" : null;
  }
  return null;
}

export function createSwitchCommand(): SlashCommand {
  return {
    name: "switch",
    description: "Switch sub-mode in current mode family",
    usage: "/switch <plan|fast|normal|team>",
    example: "/switch fast",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    completion: async () => ["plan", "fast", "normal", "team"],
    action: async (ctx, args) => {
      const switchArg = args.trim() as SwitchArg;
      if (switchArg !== "plan" && switchArg !== "fast" && switchArg !== "normal" && switchArg !== "team") {
        ctx.addItem(makeItem(ctx.sessionId, "error", "usage: /switch <plan|fast|normal|team>"));
        return;
      }

      const requestedMode = resolveRequestedMode(ctx.mode, switchArg);
      if (!requestedMode) {
        ctx.addItem(makeItem(ctx.sessionId, "error", "illegal command"));
        return;
      }

      // Check if switching mode with running team tasks
      const currentMode = ctx.mode;
      if (currentMode !== requestedMode && isTeamMode(currentMode) && ctx.hasRunningTeamTasks?.()) {
        const answers = await ctx.askQuestions(
          [
            {
              header: "模式切换",
              question: `当前有 team 任务正在运行，切换到 ${requestedMode} 模式会中断这些任务。`,
              options: [
                { label: "中断任务并切换", description: "停止当前任务，切换到新模式" },
                { label: "取消切换", description: "继续执行当前任务" },
              ],
            },
          ],
          "mode_switch_confirm",
        );

        const selected = answers[0]?.selected_options?.[0];
        if (selected !== "中断任务并切换") {
          ctx.addItem(makeItem(ctx.sessionId, "info", "模式切换已取消", "s"));
          return;
        }
        // User confirmed interrupt, send cancel request
        ctx.sendEventOnly("chat.interrupt", { intent: "cancel", mode: currentMode });
      }

      try {
        await ctx.request("mode.set", { mode: requestedMode });
      } catch {
        // Some backends still accept mode only on chat.send.
      }

      ctx.setMode(requestedMode);
      ctx.addItem(makeItem(ctx.sessionId, "info", `Mode switched to ${requestedMode}`, "s"));
    },
  };
}
