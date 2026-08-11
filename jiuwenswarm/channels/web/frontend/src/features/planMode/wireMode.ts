/**
 * Plan 模式的 wire mode 解析。
 *
 * Web UI 只保留 `agent` / `team` 两个基础模式（`AgentMode`），Plan 是一个独立的
 * 开关。发送请求时组合成后端认识的三段 canonical mode 字符串：
 *
 * ```text
 * agent + plan off -> "agent.work.normal"
 * agent + plan on  -> "agent.work.plan"
 * team             -> "team"（集群不支持 Plan）
 * ```
 *
 * P6.1：产出新三段 canonical 串（work/code 已折叠进 mode 串）。
 * `work` / `code` 仍由请求里的 `work_mode` 单独表达（决策 1：保留作项目分桶键），
 * 后端 P6.4 组合分支对新串直通 canonical——work_mode 不改变 canonical，但
 * 仍随请求发给后端作分桶。
 */

/** UI 层的基础模式。只有单 agent 支持 Plan。 */
export type PlanBaseMode = 'agent' | 'team' | 'auto_harness';

/** P6.1：单 agent + plan off 的新 canonical 串。 */
export const AGENT_WORK_NORMAL = 'agent.work.normal';
/** P6.1：单 agent + plan on 的新 canonical 串。 */
export const AGENT_WORK_PLAN = 'agent.work.plan';
/** 兼容：旧串 agent / agent.plan（后端 P3 deprecate_mode 会转新串）。 */
const LEGACY_AGENT = 'agent';
const LEGACY_AGENT_PLAN = 'agent.plan';

/**
 * Plan 只对单 agent 开放。
 *
 * 集群（`team`）不支持 Plan：集群的计划由 Leader 在团队运行时里自行编排，
 * 没有独立的计划审批流程，所以工具栏不提供 Plan 入口。
 */
export function supportsPlanMode(mode: PlanBaseMode | string | undefined): boolean {
  return mode === 'agent';
}

/**
 * 组合出发送给后端的 mode（新三段 canonical）。
 *
 * @param baseMode UI 当前的基础模式。
 * @param planActive 该会话的 Plan 开关是否打开。
 * @returns 后端认识的 wire mode（新 canonical 串）。
 */
export function resolvePlanWireMode(
  baseMode: PlanBaseMode | string | undefined,
  planActive: boolean
): string {
  const base = typeof baseMode === 'string' && baseMode ? baseMode : 'agent';
  if (!planActive || !supportsPlanMode(base)) return base;
  return AGENT_WORK_PLAN;
}

/**
 * 单 agent 普通（非 plan）态的新 canonical 串。
 *
 * Web 前端切回 agent 普通态时用 `agent.work.normal` 而非裸 `agent`，
 * 使后端 P6.4 组合分支直通 canonical（而非走旧 work_mode 组合）。
 */
export function resolveNormalWireMode(
  baseMode: PlanBaseMode | string | undefined
): string {
  const base = typeof baseMode === 'string' && baseMode ? baseMode : 'agent';
  if (supportsPlanMode(base)) return AGENT_WORK_NORMAL;
  return base; // team / auto_harness 原样
}

/** wire mode 是否处于 Plan（新旧串兼容）。 */
export function isPlanWireMode(wireMode: string | undefined): boolean {
  return wireMode === AGENT_WORK_PLAN || wireMode === LEGACY_AGENT_PLAN;
}

/** 去掉 Plan 后缀，得到基础模式（新旧串兼容）。 */
export function stripPlanSuffix(wireMode: string | undefined): string {
  if (wireMode === AGENT_WORK_PLAN || wireMode === LEGACY_AGENT_PLAN) return LEGACY_AGENT;
  return typeof wireMode === 'string' && wireMode ? wireMode : LEGACY_AGENT;
}
