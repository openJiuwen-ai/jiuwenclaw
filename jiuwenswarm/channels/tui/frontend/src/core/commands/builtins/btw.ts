/**
 * /btw (by the way) — ask a quick side question without interrupting
 * the main conversation. The backend spawns an isolated, tool-free,
 * single-turn LLM query against the current conversation context
 * and returns just the answer.
 */
import { addError, addInfo } from "../helpers.js";
import { CommandKind, type CommandContext, type SlashCommand } from "../types.js";

interface BtwResponse {
  status: "ok" | "no_context" | "failed";
  answer?: string;
  error?: string;
}

const NO_CONTEXT_MSG = "No conversation context available yet — send a message first.";
const FAILED_MSG = "Couldn't answer the side question. Please try again or ask in the main conversation.";
const EMPTY_QUESTION_MSG = "Usage: /btw <your question>";

export function createBtwCommand(): SlashCommand {
  return {
    name: "btw",
    description: "Ask a quick side question without interrupting the main conversation",
    usage: "/btw <question>",
    example: "/btw what does git status do?",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const question = args?.trim();
      if (!question) {
        ctx.addItem(addError(ctx.sessionId, EMPTY_QUESTION_MSG));
        return;
      }

      // Dim indicator while the side query is running
      const thinkingId = `btw-thinking-${Date.now()}`;
      ctx.addItem({
        kind: "info",
        id: thinkingId,
        sessionId: ctx.sessionId,
        content: `Answering: ${question}`,
        icon: "💭",
        at: new Date().toISOString(),
        meta: { view: "dim" as const },
      });

      try {
        const payload = await ctx.request<BtwResponse>(
          "command.btw",
          { question, mode: ctx.mode },
          120000,
        );

        switch (payload.status) {
          case "ok":
            if (payload.answer) {
              ctx.addItem(
                addInfo(ctx.sessionId, `💡 /btw ${question}\n\n${payload.answer}`),
              );
            } else {
              ctx.addItem(addInfo(ctx.sessionId, "(empty answer)", "💡"));
            }
            break;
          case "no_context":
            ctx.addItem(addInfo(ctx.sessionId, NO_CONTEXT_MSG, "i"));
            break;
          case "failed":
            ctx.addItem(addError(ctx.sessionId, payload.error || FAILED_MSG));
            break;
          default:
            ctx.addItem(addError(ctx.sessionId, FAILED_MSG));
        }
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `btw failed: ${message}`));
      }
    },
  };
}
