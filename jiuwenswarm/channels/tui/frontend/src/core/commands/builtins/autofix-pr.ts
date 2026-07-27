import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";
import { buildAutofixPrPrompt } from "./autofix-pr.prompts.js";
import { isWorkingTreeClean, resolvePrContext } from "./autofix-pr.status.js";

/**
 * /autofix-pr - Drive the current branch's open PR to green.
 *
 * The prompt is built in the TUI (like /init) and sent straight to the agent,
 * which reads the failing checks and review comments (via `gh` on GitHub, REST
 * on GitCode), fixes the root cause, and pushes to the PR branch. This command
 * is inherently local — it needs the checkout, git and `gh` — so it lives
 * entirely TUI-side rather than round-tripping a prompt through the server.
 *
 * `--watch` keeps re-running rounds on an interval until the PR is green /
 * merged / closed (or a round-budget fuse trips); `--stop` cancels a watch.
 * Requires code mode (file editing + git).
 */
export function createAutofixPrCommand(): SlashCommand {
  return {
    name: "autofix-pr",
    description:
      "Fix the open PR for the current branch until CI passes and review comments are addressed",
    usage: "/autofix-pr [PR number or URL] [--watch] [--stop]",
    example: "/autofix-pr --watch",
    argGuide: "[optional: PR number or URL; --watch to keep fixing until green; --stop to cancel]",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const zh = ctx.preferredLanguage === "zh";

      // --stop cancels a running watch; allowed regardless of mode.
      if (/(^|\s)--stop(\s|$)/.test(args)) {
        const stopped = ctx.stopPrWatch?.() ?? false;
        ctx.addItem(
          addInfo(
            ctx.sessionId,
            stopped
              ? zh ? "已停止 PR watch。" : "PR watch stopped."
              : zh ? "当前没有在运行的 PR watch。" : "No PR watch is running.",
            "i",
          ),
        );
        return;
      }

      if (!ctx.mode.startsWith("code.")) {
        ctx.addItem(
          addError(
            ctx.sessionId,
            zh
              ? "/autofix-pr 需要在 code 模式下运行。请先执行 /mode code 切到 code 模式再重试。"
              : "/autofix-pr requires code mode. Run /mode code first, then try again.",
          ),
        );
        return;
      }

      const watch = /(^|\s)--watch(\s|$)/.test(args);
      const prArg = args.replace(/(^|\s)--watch(\s|$)/g, " ").replace(/(^|\s)--stop(\s|$)/g, " ").trim();

      if (!watch) {
        // Single round: build the prompt locally and send it once.
        const prompt = buildAutofixPrPrompt({ prArg });
        const requestId = ctx.sendMessage(prompt, undefined, ctx.mode, { logAsUser: false });
        if (!requestId) {
          ctx.addItem(
            addInfo(
              ctx.sessionId,
              zh
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
            zh
              ? prArg ? `正在修复 PR ${prArg}…` : "正在修复当前分支的 PR…"
              : prArg ? `Fixing PR ${prArg}…` : "Fixing the PR for the current branch…",
            "i",
          ),
        );
        return;
      }

      // ── Watch mode ──────────────────────────────────────────────────────
      if (!ctx.startPrWatch) {
        ctx.addItem(addError(ctx.sessionId, zh ? "当前环境不支持 PR watch。" : "PR watch is not supported here."));
        return;
      }

      const projectDir = ctx.getCurrentProjectDir();

      // Guardrail: a watch auto-commits & pushes unattended, so refuse on a dirty
      // tree — otherwise it could sweep in or act on uncommitted work.
      if (!(await isWorkingTreeClean(projectDir))) {
        ctx.addItem(
          addError(
            ctx.sessionId,
            zh
              ? "工作区有未提交改动。PR watch 会无人值守地自动改代码并 push，请先提交或 stash 后再开 watch。"
              : "Working tree has uncommitted changes. PR watch commits and pushes unattended — commit or stash first.",
          ),
        );
        return;
      }

      // Resolve which PR this watch follows (from the current branch, or the arg).
      const prCtx = await resolvePrContext(projectDir, prArg);
      if (!prCtx.isComplete) {
        ctx.addItem(
          addError(
            ctx.sessionId,
            zh
              ? "找不到当前分支对应的开放 PR。请确认已推送分支并开了 PR，或用 /autofix-pr <PR号> --watch 指定。"
              : "No open PR found for the current branch. Push the branch and open a PR, or pass /autofix-pr <number> --watch.",
          ),
        );
        return;
      }

      ctx.startPrWatch({ repo: prCtx.repo, prNumber: prCtx.prNumber, platform: prCtx.platform });
      ctx.addItem(
        addInfo(
          ctx.sessionId,
          zh
            ? `已开始盯 PR ${prCtx.prNumber}（${prCtx.repo}）：每 5 分钟复查一轮，绿/合并/关闭即停（最多 12 轮，可 /autofix-pr --stop 中止）。`
            : `Watching PR ${prCtx.prNumber} (${prCtx.repo}): re-checks every 5 min, stops on green/merged/closed (max 12 rounds; /autofix-pr --stop to cancel).`,
          "i",
        ),
      );
    },
  };
}
