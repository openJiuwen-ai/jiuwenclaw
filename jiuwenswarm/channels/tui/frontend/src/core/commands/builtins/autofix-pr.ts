import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";

interface AutofixPrResponse {
  prompt?: string;
  error?: string;
}

/**
 * /autofix-pr - Drive the current branch's open PR to green.
 *
 * Fetches the autofix prompt from the server (single source of truth, same as
 * /simplify) and forwards it to the agent, which reads the failing checks and
 * review comments via `gh`, fixes the root cause, and pushes to the PR branch.
 * Requires code mode (file editing + git).
 */
export function createAutofixPrCommand(): SlashCommand {
  return {
    name: "autofix-pr",
    description:
      "Fix the open PR for the current branch until CI passes and review comments are addressed",
    usage: "/autofix-pr [PR number or URL]",
    example: "/autofix-pr",
    argGuide: "[optional: PR number or URL; omit to infer from current branch]",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      if (!ctx.mode.startsWith("code.")) {
        ctx.addItem(
          addError(
            ctx.sessionId,
            ctx.preferredLanguage === "zh"
              ? "/autofix-pr 需要在 code 模式下运行。请先执行 /mode code 切到 code 模式再重试。"
              : "/autofix-pr requires code mode. Run /mode code first, then try again.",
          ),
        );
        return;
      }

      const prArg = args.trim();

      ctx.setRunningCommand?.("autofix-pr");
      try {
        const payload = await ctx.request<AutofixPrResponse>(
          "command.autofix_pr",
          { pr_arg: prArg },
          30000,
        );

        if (payload?.error) {
          ctx.addItem(addError(ctx.sessionId, `autofix-pr failed: ${payload.error}`));
          return;
        }

        const prompt = payload?.prompt;
        if (!prompt || !prompt.trim()) {
          ctx.addItem(
            addError(ctx.sessionId, "autofix-pr failed: empty prompt from server"),
          );
          return;
        }

        const requestId = ctx.sendMessage(prompt, undefined, ctx.mode, {
          logAsUser: false,
        });
        if (!requestId) {
          ctx.addItem(
            addInfo(
              ctx.sessionId,
              ctx.preferredLanguage === "zh"
                ? "当前离线，/autofix-pr 请求未发送；网络恢复后请重试。"
                : "Offline; /autofix-pr request not sent. Please retry after reconnecting.",
              "p",
            ),
          );
          return;
        }

        ctx.addItem(
          addInfo(
            ctx.sessionId,
            ctx.preferredLanguage === "zh"
              ? prArg
                ? `正在修复 PR ${prArg}…`
                : "正在修复当前分支的 PR…"
              : prArg
                ? `Fixing PR ${prArg}…`
                : "Fixing the PR for the current branch…",
            "i",
          ),
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `autofix-pr failed: ${message}`));
      } finally {
        ctx.setRunningCommand?.(null);
      }
    },
  };
}
