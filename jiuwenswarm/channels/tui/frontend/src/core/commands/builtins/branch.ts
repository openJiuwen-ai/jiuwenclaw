import { generateSessionId } from "../../session-state.js";
import { addCommandEcho, addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

export interface SessionForkPayload {
  session_id?: string;
  source_session_id?: string;
  title?: string;
}

export function createBranchCommand(): SlashCommand {
  return {
    name: "branch",
    altNames: ["fork"],
    description: "Create a branch of the current conversation at this point",
    usage: "/branch [name]",
    example: "/branch fix-login-bug",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      if (ctx.isProcessing) {
        ctx.addItem(
          addError(ctx.sessionId, "session is busy; stop the current run before branching"),
        );
        return;
      }

      if (ctx.entries.length === 0) {
        ctx.addItem(addError(ctx.sessionId, "no conversation to branch from"));
        return;
      }

      const customTitle = args.trim();
      const targetId = generateSessionId();

      try {
        const payload = await ctx.request<SessionForkPayload>("session.fork", {
          source_session_id: ctx.sessionId,
          target_session_id: targetId,
          title: customTitle || undefined,
        });

        const forkSessionId = payload.session_id || targetId;

        const originalSessionId = ctx.sessionId;
        ctx.updateSession(forkSessionId);
        ctx.clearEntries();
        ctx.addItem(addCommandEcho(forkSessionId, "/branch"));
        ctx.addItem(
          addInfo(
            forkSessionId,
            "You are now in the new branch " +
              `(session ${forkSessionId}).\n` +
              `Use /resume ${originalSessionId} to return to the original.`,
            "i",
          ),
        );
        ctx.setSessionTitle(payload.title || "");
        await ctx.restoreHistory(forkSessionId);
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `branch failed: ${message}`));
      }
    },
  };
}
