import type { AutocompleteItem } from "@mariozechner/pi-tui";
import type { ClientMode } from "../../modes.js";
import { makeItem } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

/** TUI `/mode` 树形展示；分组行 value 为 `agent`/`code`（与 modeAlias 默认一致），不修改 pi-tui。 */
export function buildModeAutocompleteItems(): AutocompleteItem[] {
  return [
    { value: "agent", label: "agent" },
    { value: "agent.plan", label: "    plan" },
    { value: "agent.fast", label: "    fast" },
    { value: "code", label: "code" },
    { value: "code.normal", label: "    normal" },
    { value: "code.plan", label: "    plan" },
    { value: "code.team", label: "    team" },
    { value: "team", label: "team" },
  ];
}

export function createModeCommand(): SlashCommand {
  const directModes = [
    "agent",
    "code",
    "agent.plan",
    "agent.fast",
    "code.plan",
    "code.normal",
    "code.team",
    "team",
  ] as const;
  /** 用户输入的简写 → 实际会话模式（/mode agent → agent.plan，/mode code → code.normal）。 */
  const modeAlias: Record<string, ClientMode> = {
    plan: "agent.plan",
    agent: "agent.plan",
    code: "code.normal",
    "agent.plan": "agent.plan",
    "agent.fast": "agent.fast",
    "code.plan": "code.plan",
    "code.normal": "code.normal",
    "code.team": "code.team",
    team: "team",
  };

  return {
    name: "mode",
    description: "Switch chat mode",
    usage: "/mode <agent|code|code.team|team>",
    example: "/mode code.team",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    completion: async () => [...directModes],
    action: async (ctx, args) => {
      const requestedMode = args.trim();
      // 无参数时显示当前 mode
      if (!requestedMode) {
        const currentMode = ctx.mode ?? "unknown";
        ctx.addItem(
          makeItem(
            ctx.sessionId,
            "info",
            `Current mode: ${currentMode}`,
            "m",
          ),
        );
        return;
      }
      const nextMode = modeAlias[requestedMode];
      if (!nextMode) {
        ctx.addItem(
          makeItem(
            ctx.sessionId,
            "error",
            "usage: /mode <agent|code|code.team|team>",
          ),
        );
        return;
      }
      try {
        await ctx.request("mode.set", { mode: nextMode });
      } catch {
        // Some backends still accept mode only on chat.send.
      }
      ctx.setMode(nextMode);
      ctx.addItem(makeItem(ctx.sessionId, "info", `Mode set to ${nextMode}`, "m"));
    },
  };
}
