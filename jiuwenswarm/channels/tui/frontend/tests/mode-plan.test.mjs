// P5 门控：TUI 前端新 canonical 模式串接通测试。
// 覆盖 ClientMode 新串类型守卫、isTeamMode 扩展、resolveNormalTarget 对称退出、
// resolvePlanTarget 不回归、isPlanVariant 新旧串。
import assert from "node:assert/strict";

import {
  isClientMode,
  isTeamMode,
  formatModeForDisplay,
} from "../dist/core/modes.js";
import {
  resolvePlanTarget,
  resolveNormalTarget,
  isPlanVariant,
} from "../dist/core/commands/builtins/plan.js";

// ── P5.1：ClientMode 新串类型守卫 ─────────────────────────────────────────
const NEW_CANONICAL = [
  "agent.work.normal",
  "agent.work.plan",
  "agent.code.normal",
  "agent.code.plan",
  "team.work.normal",
  "team.work.plan",
  "team.code.normal",
  "team.code.plan",
];
const LEGACY = [
  "agent.plan",
  "agent.fast",
  "code.plan",
  "code.normal",
  "code.team",
  "team",
  "team.plan",
  "team.plan.normal",
  "team.plan.code",
];

for (const mode of [...NEW_CANONICAL, ...LEGACY]) {
  assert.equal(isClientMode(mode), true, `isClientMode(${mode}) should be true`);
}
assert.equal(isClientMode("unknown_mode"), false);
assert.equal(isClientMode(""), false);

// ── P5.1：isTeamMode 新串 ──────────────────────────────────────────────────
const NEW_TEAM = [
  "team.work.normal",
  "team.work.plan",
  "team.code.normal",
  "team.code.plan",
];
for (const mode of NEW_TEAM) {
  assert.equal(isTeamMode(mode), true, `isTeamMode(${mode}) should be true`);
}
// agent.* 新串不是 team
for (const mode of ["agent.work.normal", "agent.code.plan"]) {
  assert.equal(isTeamMode(mode), false, `isTeamMode(${mode}) should be false`);
}

// ── formatModeForDisplay 不回归（P5.1 不动它）──────────────────────────────
assert.equal(formatModeForDisplay("code.team"), "team.code");
assert.equal(formatModeForDisplay("team.plan.code"), "team.plan.code");
// 新串原样输出（formatModeForDisplay 不做新串转译）
assert.equal(formatModeForDisplay("agent.work.plan"), "agent.work.plan");
assert.equal(formatModeForDisplay("team.code.normal"), "team.code.normal");

// ── P5.2：resolvePlanTarget 不回归（旧串）──────────────────────────────────
assert.equal(resolvePlanTarget("team"), "team.plan.normal");
assert.equal(resolvePlanTarget("team.plan"), "team.plan.normal");
assert.equal(resolvePlanTarget("team.plan.normal"), "team.plan.normal");
assert.equal(resolvePlanTarget("code.team"), "team.plan.code");
assert.equal(resolvePlanTarget("team.plan.code"), "team.plan.code");
assert.equal(resolvePlanTarget("code.normal"), "code.plan");
assert.equal(resolvePlanTarget("agent.fast"), "agent.plan");

// ── P5.2：resolvePlanTarget 新串 ───────────────────────────────────────────
assert.equal(resolvePlanTarget("agent.work.normal"), "agent.work.plan");
assert.equal(resolvePlanTarget("agent.code.normal"), "agent.code.plan");
assert.equal(resolvePlanTarget("team.work.normal"), "team.work.plan");
assert.equal(resolvePlanTarget("team.code.normal"), "team.code.plan");
// plan 变体自身：resolvePlanTarget 应返回自身（/plan 在 plan 态走 resolveNormalTarget）
assert.equal(resolvePlanTarget("agent.work.plan"), "agent.work.plan");
assert.equal(resolvePlanTarget("team.code.plan"), "team.code.plan");

// ── P5.2：resolveNormalTarget 对称退出（plan→normal）──────────────────────
// 旧串
assert.equal(resolveNormalTarget("agent.plan"), "agent.fast");
assert.equal(resolveNormalTarget("code.plan"), "code.normal");
assert.equal(resolveNormalTarget("team.plan.normal"), "team");
assert.equal(resolveNormalTarget("team.plan.code"), "code.team");
// 新串
assert.equal(resolveNormalTarget("agent.work.plan"), "agent.work.normal");
assert.equal(resolveNormalTarget("agent.code.plan"), "agent.code.normal");
assert.equal(resolveNormalTarget("team.work.plan"), "team.work.normal");
assert.equal(resolveNormalTarget("team.code.plan"), "team.code.normal");
// 非 plan 模式原样返回
assert.equal(resolveNormalTarget("agent.work.normal"), "agent.work.normal");
assert.equal(resolveNormalTarget("team"), "team");

// ── P5：isPlanVariant 新旧串 ───────────────────────────────────────────────
const PLAN_MODES = [
  "agent.plan",
  "code.plan",
  "team.plan.normal",
  "team.plan.code",
  "agent.work.plan",
  "agent.code.plan",
  "team.work.plan",
  "team.code.plan",
];
for (const mode of PLAN_MODES) {
  assert.equal(isPlanVariant(mode), true, `isPlanVariant(${mode}) should be true`);
}
const NORMAL_MODES = [
  "agent.fast",
  "code.normal",
  "code.team",
  "team",
  "agent.work.normal",
  "agent.code.normal",
  "team.work.normal",
  "team.code.normal",
];
for (const mode of NORMAL_MODES) {
  assert.equal(isPlanVariant(mode), false, `isPlanVariant(${mode}) should be false`);
}

console.log("mode-plan tests passed");
