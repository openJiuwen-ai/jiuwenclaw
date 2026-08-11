// P6.1 门控：Web 前端 wireMode 产出新三段 canonical 串。
import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  resolvePlanWireMode,
  resolveNormalWireMode,
  isPlanWireMode,
  stripPlanSuffix,
  supportsPlanMode,
  AGENT_WORK_NORMAL,
  AGENT_WORK_PLAN,
} from '../node_modules/.cache/wire-mode/features/planMode/wireMode.js';

test('resolvePlanWireMode: agent + plan on → agent.work.plan (新 canonical)', () => {
  assert.equal(resolvePlanWireMode('agent', true), AGENT_WORK_PLAN);
  assert.equal(resolvePlanWireMode('agent', true), 'agent.work.plan');
});

test('resolvePlanWireMode: agent + plan off → 裸 agent (旧式,由 resolveNormalWireMode 覆盖)', () => {
  // resolvePlanWireMode 在 plan off 时返回 baseMode 原样；实际出站走 resolveNormalWireMode
  assert.equal(resolvePlanWireMode('agent', false), 'agent');
});

test('resolveNormalWireMode: agent → agent.work.normal (新 canonical)', () => {
  assert.equal(resolveNormalWireMode('agent'), AGENT_WORK_NORMAL);
  assert.equal(resolveNormalWireMode('agent'), 'agent.work.normal');
  assert.equal(resolveNormalWireMode(undefined), 'agent.work.normal');
});

test('resolveNormalWireMode: team/auto_harness 原样透传（不参与新串组合）', () => {
  assert.equal(resolveNormalWireMode('team'), 'team');
  assert.equal(resolveNormalWireMode('auto_harness'), 'auto_harness');
});

test('isPlanWireMode: 新旧 plan 串都识别', () => {
  assert.equal(isPlanWireMode('agent.work.plan'), true);
  assert.equal(isPlanWireMode('agent.plan'), true); // 兼容旧串
  assert.equal(isPlanWireMode('agent.work.normal'), false);
  assert.equal(isPlanWireMode('agent'), false);
  assert.equal(isPlanWireMode(undefined), false);
});

test('stripPlanSuffix: 新旧 plan 串都去掉后缀回 agent', () => {
  assert.equal(stripPlanSuffix('agent.work.plan'), 'agent');
  assert.equal(stripPlanSuffix('agent.plan'), 'agent');
  assert.equal(stripPlanSuffix('agent.work.normal'), 'agent.work.normal');
  assert.equal(stripPlanSuffix(undefined), 'agent');
});

test('supportsPlanMode: 只 agent 开放 plan', () => {
  assert.equal(supportsPlanMode('agent'), true);
  assert.equal(supportsPlanMode('team'), false);
  assert.equal(supportsPlanMode('auto_harness'), false);
});
