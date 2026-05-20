import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

interface RecapResponse {
  status: "ok" | "no_turn" | "failed";
  summary?: string;
  error?: string;
}

const NO_TURN_MSG = "Nothing to recap yet — send a message first.";
const FAILED_MSG = "Couldn't generate a recap. Please try again later.";

export function createRecapCommand(): SlashCommand {
  return {
    name: "recap",
    description: "Generate a one-line session recap now",
    usage: "/recap",
    example: "/recap",
    kind: CommandKind.BUILT_IN,
    takesArgs: false,
    action: async (ctx) => {
      ctx.addItem(addInfo(ctx.sessionId, "Recaping...", "⏳"));

      try {
        const payload = await ctx.request<RecapResponse>(
          "command.recap",
          { mode: ctx.mode },
          60000,
        );

        switch (payload.status) {
          case "ok":
            ctx.addItem(addInfo(ctx.sessionId, `※ ${payload.summary}`, "※"));
            break;
          case "no_turn":
            ctx.addItem(addInfo(ctx.sessionId, NO_TURN_MSG, "i"));
            break;
          case "failed":
            ctx.addItem(addError(ctx.sessionId, FAILED_MSG));
            break;
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `recap failed: ${message}`));
      }
    },
  };
}