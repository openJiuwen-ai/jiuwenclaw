import { addInfo } from "../helpers.js";
import type { ClientMode } from "../../modes.js";
import { CommandKind, type SlashCommand } from "../types.js";

const CODE_MODES = new Set(["code.normal", "code.team", "code.plan"]);

/** Resolve the plan variant while preserving the current agent/team profile. */
export function resolvePlanTarget(mode: ClientMode): ClientMode {
  if (mode === "team" || mode === "team.plan" || mode === "team.plan.normal") {
    return "team.plan.normal";
  }
  if (mode === "code.team" || mode === "team.plan.code") {
    return "team.plan.code";
  }
  // P5：新 team plan 串直接对称——已在 plan 变体则原样（/plan 在 plan 态走 resolveNormalTarget）。
  if (mode === "team.work.normal" || mode === "team.work.plan") {
    return "team.work.plan";
  }
  if (mode === "team.code.normal" || mode === "team.code.plan") {
    return "team.code.plan";
  }
  // P5：新 agent code 串走 code plan 变体。
  if (mode === "agent.code.normal" || mode === "agent.code.plan") {
    return "agent.code.plan";
  }
  if (mode === "agent.work.normal" || mode === "agent.work.plan") {
    return "agent.work.plan";
  }
  return CODE_MODES.has(mode) ? "code.plan" : "agent.plan";
}

/**
 * P5.2：plan 变体 → 对应 normal 变体（对称退出）。
 * 与 resolvePlanTarget 配对：/plan 在 plan 态切 normal，在 normal 态切 plan。
 */
export function resolveNormalTarget(mode: ClientMode): ClientMode {
  // 旧串
  if (mode === "agent.plan") return "agent.fast";
  if (mode === "code.plan") return "code.normal";
  if (mode === "team.plan.normal") return "team";
  if (mode === "team.plan.code") return "code.team";
  // 新串：去 .plan 后缀换 .normal
  if (mode === "agent.work.plan") return "agent.work.normal";
  if (mode === "agent.code.plan") return "agent.code.normal";
  if (mode === "team.work.plan") return "team.work.normal";
  if (mode === "team.code.plan") return "team.code.normal";
  // 非 plan 模式：原样返回（调用方应先判 isPlanMode）
  return mode;
}

/**
 * P5：判断当前 mode 是否为 plan 变体（新旧串）。
 * 与 app-state.isPlanClientMode / screen-layout.isPlanMode 同源。
 *
 * ⚠️ 不能只用 `endsWith(".plan")`：旧 team plan 约定是 `team.plan.<profile>`
 * （plan 在第二段，如 team.plan.normal / team.plan.code），endsWith 抓不到。
 * 新约定是 `<role>.<env>.plan`（plan 在第三段）。组合两个前缀/后缀判定覆盖
 * 两种约定：新串 endsWith(".plan")，旧 team 串 startsWith("team.plan")。
 */
export function isPlanVariant(mode: ClientMode): boolean {
  return mode.endsWith(".plan") || mode.startsWith("team.plan");
}

export function createPlanCommand(): SlashCommand {
  return {
    name: "plan",
    description: "Switch to plan mode, or send a planning request",
    usage: "/plan [open|<description>]",
    example: "/plan outline the migration steps",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: (ctx, args) => {
      const value = args.trim();
      // P5.2：对称退出——plan 态切 normal，normal 态切 plan。
      const inPlan = isPlanVariant(ctx.mode);
      const target = inPlan ? resolveNormalTarget(ctx.mode) : resolvePlanTarget(ctx.mode);
      if (ctx.mode !== target) {
        ctx.setMode(target);
      }
      ctx.markPlanEntryFromSlashCommand?.();

      // 对称退出：从 plan 切回 normal 时，不发 planning request，仅切模式。
      if (inPlan) {
        ctx.addItem(addInfo(ctx.sessionId, "Plan mode exited", "p"));
        return;
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
