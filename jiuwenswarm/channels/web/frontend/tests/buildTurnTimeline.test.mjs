import assert from 'node:assert/strict';
import test from 'node:test';

import { buildRenderItems, buildTimelineItems } from '../node_modules/.cache/build-turn-timeline/buildTurnTimeline.js';
import { parseHistoryJsonFileToTimelinePreview } from '../node_modules/.cache/build-turn-timeline/historyRestore.js';

const U = 1_700_000_000_000; // 用户消息时刻
const S = 1_700_000_005_000; // reasoning 首帧
const A = 1_700_000_035_000; // reasoning 末帧（updatedAt）

function iso(ms) {
  return new Date(ms).toISOString();
}

function userMessage(ms, id = 'u1') {
  return {
    type: 'message',
    key: id,
    timestampMs: ms,
    sourceIndex: 0,
    message: { id, role: 'user', content: 'hi', timestamp: iso(ms) },
  };
}

function reasoningItem(segment, sourceIndex = 0) {
  return {
    type: 'reasoning',
    key: segment.id,
    timestampMs: segment.startedAt,
    sourceIndex,
    segment,
  };
}

function turnSummaryOf(items) {
  return items.find((item) => item.type === 'turnSummary');
}

function execution({ status, startedAt, updatedAt }) {
  return {
    toolCallId: `tc-${startedAt}`,
    toolCall: { id: `tc-${startedAt}`, name: 'bash', arguments: {} },
    status,
    startedAt: iso(startedAt),
    updatedAt: iso(updatedAt),
    timeoutAt: iso(startedAt + 60_000),
  };
}

function message(id, role, content, ms) {
  return { id, role, content, timestamp: iso(ms) };
}

function a2uiClientEvent(actionName) {
  return JSON.stringify({
    type: 'a2ui.client_event',
    protocolVersion: '0.8',
    event: { userAction: { name: actionName } },
  });
}

test('异常结束（无 closedAt）：reasoning.updatedAt 兜底为耗时终点', () => {
  const items = [
    userMessage(U),
    reasoningItem({
      id: 'rsn1',
      text: 'thinking…',
      startedAt: S,
      closed: false,
      updatedAt: A,
    }),
  ];
  const out = buildRenderItems(items, false, false);
  const summary = turnSummaryOf(out);
  assert.ok(summary, 'should emit turnSummary');
  assert.equal(summary.workEndMs, A, 'workEndMs 落在末帧 updatedAt');
  assert.equal(summary.startMs, U);
});

test('老数据向后兼容：无 updatedAt 时用 closedAt', () => {
  const closedAt = 1_700_000_020_000;
  const items = [
    userMessage(U),
    reasoningItem({
      id: 'rsn1',
      text: 'thinking…',
      startedAt: S,
      closed: true,
      closedAt,
    }),
  ];
  const summary = turnSummaryOf(buildRenderItems(items, false, false));
  assert.equal(summary.workEndMs, closedAt, '缺失 updatedAt 时退回 closedAt');
});

test('哨兵值：updatedAt 为 0 或过小毫秒数被忽略', () => {
  const closedAt = 1_700_000_020_000;
  for (const bad of [0, 500, 1_000_000]) {
    const items = [
      userMessage(U),
      reasoningItem({
        id: `rsn-${bad}`,
        text: 'thinking…',
        startedAt: S,
        closed: true,
        updatedAt: bad,
        closedAt,
      }),
    ];
    const summary = turnSummaryOf(buildRenderItems(items, false, false));
    assert.equal(summary.workEndMs, closedAt, `updatedAt=${bad} 不应撑爆耗时`);
  }
});

test('回归：pending/timeout 工具的 updatedAt 不计入耗时终点（防巡检污染）', () => {
  const toolStart = 1_700_000_010_000;
  const hugePollution = 1_900_000_000_000; // 巡检写成 Date.now() 的假时间
  const items = [
    userMessage(U),
    {
      type: 'toolExecution',
      key: 'tc-1',
      timestampMs: toolStart,
      sourceIndex: 0,
      execution: execution({ status: 'pending', startedAt: toolStart, updatedAt: hugePollution }),
    },
  ];
  const summary = turnSummaryOf(buildRenderItems(items, false, false));
  assert.equal(summary.workEndMs, toolStart, 'pending 的 updatedAt 不得进入 work 终点');
});

test('A2UI 交互事件隐藏展示但保留独立轮次边界', () => {
  const firstActionAt = U + 20_000;
  const secondActionAt = U + 40_000;
  const thirdActionAt = U + 60_000;
  const messages = [
    message('user-initial', 'user', '生成注册表单', U),
    message('assistant-form', 'assistant', '<a2ui-json>[]</a2ui-json>', U + 10_000),
    message('a2ui-action-1', 'user', a2uiClientEvent('submitRegistration'), firstActionAt),
    message('assistant-result-1', 'assistant', '第一次注册结果', U + 30_000),
    message('a2ui-action-2', 'user', a2uiClientEvent('submitRegistration'), secondActionAt),
    message('assistant-result-2', 'assistant', '第二次注册结果', U + 50_000),
    message('a2ui-action-3', 'user', a2uiClientEvent('submitRegistration'), thirdActionAt),
    message('assistant-result-3', 'assistant', '第三次注册结果', U + 70_000),
  ];
  const reasoningSegments = [
    { id: 'reasoning-form', text: 'form', startedAt: U + 5_000, closed: true },
    { id: 'reasoning-result-1', text: 'result 1', startedAt: U + 25_000, closed: true },
    { id: 'reasoning-result-2', text: 'result 2', startedAt: U + 45_000, closed: true },
    { id: 'reasoning-result-3', text: 'result 3', startedAt: U + 65_000, closed: true },
  ];

  const rendered = buildRenderItems(buildTimelineItems(messages, [], reasoningSegments), false, false);
  const assistantItems = rendered.filter(item => item.type === 'message' && item.message.role === 'assistant');
  const actionItems = rendered.filter(item => item.type === 'message' && item.message.id.startsWith('a2ui-action-'));
  const reasoningItems = rendered.filter(item => item.type === 'reasoning');
  const summaries = rendered.filter(item => item.type === 'turnSummary');

  assert.deepEqual(
    assistantItems.map(item => item.turnId),
    [1, 2, 3, 4],
  );
  assert.deepEqual(
    assistantItems.map(item => item.hideMeta),
    [false, false, false, false],
  );
  assert.deepEqual(
    actionItems.map(item => item.hidden),
    [true, true, true],
  );
  assert.deepEqual(
    reasoningItems.map(item => item.turnId),
    [1, 2, 3, 4],
  );
  assert.deepEqual(
    summaries.map(item => item.startMs),
    [U, firstActionAt, secondActionAt, thirdActionAt],
  );
});

test('普通用户轮次和主动推荐仍保持原有分组', () => {
  const messages = [
    message('user-1', 'user', '第一问', U),
    message('assistant-1', 'assistant', '第一答', U + 10_000),
    message('user-2', 'user', '第二问', U + 20_000),
    message('assistant-2', 'assistant', '第二答', U + 30_000),
    {
      ...message('proactive', 'assistant', '主动推荐', U + 40_000),
      isProactiveRecommendation: true,
    },
  ];

  const rendered = buildRenderItems(buildTimelineItems(messages, [], []), false, false);
  const assistantItems = rendered.filter(item => item.type === 'message' && item.message.role === 'assistant');

  assert.deepEqual(
    assistantItems.map(item => item.turnId),
    [1, 2, 3],
  );
  assert.deepEqual(
    assistantItems.map(item => item.hideMeta),
    [false, false, false],
  );
  assert.deepEqual(
    assistantItems.map(item => item.hidden),
    [false, false, false],
  );
});

test('历史恢复保留 A2UI 隐藏边界，多次回复刷新后仍属于独立轮次', () => {
  const records = [
    { role: 'user', content: '生成注册表单', timestamp: iso(U) },
    { role: 'assistant', content: '<a2ui-json>[]</a2ui-json>', timestamp: iso(U + 10_000) },
    {
      role: 'user',
      content: JSON.parse(a2uiClientEvent('submitRegistration')),
      timestamp: iso(U + 20_000),
    },
    { role: 'assistant', content: '第一次注册结果', timestamp: iso(U + 30_000) },
    {
      role: 'user',
      content: JSON.parse(a2uiClientEvent('submitRegistration')),
      timestamp: iso(U + 40_000),
    },
    { role: 'assistant', content: '第二次注册结果', timestamp: iso(U + 50_000) },
  ];

  const preview = parseHistoryJsonFileToTimelinePreview(records, 'a2ui-history');
  const rendered = buildRenderItems(buildTimelineItems(preview.messages, preview.executions, preview.reasoningSegments), false, false);
  const assistantItems = rendered.filter(item => item.type === 'message' && item.message.role === 'assistant');
  const hiddenUserItems = rendered.filter(item => item.type === 'message' && item.message.role === 'user' && item.hidden);

  assert.equal(hiddenUserItems.length, 2);
  assert.deepEqual(
    assistantItems.map(item => item.turnId),
    [1, 2, 3],
  );
  assert.deepEqual(
    assistantItems.map(item => item.hideMeta),
    [false, false, false],
  );
});
