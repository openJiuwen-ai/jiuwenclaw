import assert from 'node:assert/strict';
import test from 'node:test';
import {
  nodeChangeDisplayLabel,
  nodeStageLabel,
  nodeScoreLines,
  presentRsiNode,
} from '../node_modules/.cache/rsi-presentation/rsiPresentation.mjs';

const context = (scenario, artifactType, nodes, taskRunning = false) => ({
  scenario,
  artifactType,
  allNodes: nodes,
  taskRunning,
});

test('paper nodes use a stable version title and expose the stage separately', () => {
  const root = {
    node_id: 'ROOT',
    iteration: 0,
    parent_id: null,
    type: 'ROOT',
    adopted: true,
    score: null,
    description: 'Uploaded starting paper (ingestion not yet wired in).',
    changes: [],
    extra: {},
  };
  const candidate = {
    node_id: 'artifact:task:node:1',
    iteration: 1,
    parent_id: 'ROOT',
    type: 'PROVISIONAL',
    adopted: false,
    score: null,
    description: '正在撰写论文',
    failure_reason: null,
    failure_class: null,
    changes: [{ group: 'PAPER', operation: 'generate', summary: 'Generated a new paper version.' }],
    extra: {
      paper: { round_index: 1, attempt: 1, outcome: 'pending' },
      stage: { id: 'reporting', name: '正在撰写论文' },
    },
  };
  const presentation = presentRsiNode(candidate, context('ARTIFACT', 'PAPER', [root, candidate], true));
  const rootPresentation = presentRsiNode(root, context('ARTIFACT', 'PAPER', [root, candidate], true));
  assert.equal(presentation.title, '第 1 轮 · 论文候选');
  assert.equal(presentation.lifecycle, 'generating');
  assert.equal(presentation.stageLabel, '撰写中');
  assert.equal(presentation.summary, '生成新的论文版本');
  assert.equal(nodeStageLabel(candidate), '撰写中');
  assert.equal(rootPresentation.summary, '已上传起始论文');
});

test('baseline nodes use a semantic subtitle and accept legacy string stages', () => {
  const root = {
    node_id: 'ROOT',
    iteration: 0,
    parent_id: null,
    type: 'ROOT',
    adopted: true,
    description: 'paper optimization root',
    changes: [],
    extra: { stage: 'research' },
  };
  const presentation = presentRsiNode(root, context('ARTIFACT', 'PAPER', [root]));

  assert.equal(presentation.title, '基线论文');
  assert.equal(presentation.subtitle, '优化起点');
  assert.equal(presentation.stageLabel, '调研中');
  assert.equal(presentation.summary, '从基线开始生成论文');
});

test('in-flight artifact generation is labeled as generation, not evaluation', () => {
  const node = {
    node_id: 'rsi:node:2',
    parent_id: 'ROOT',
    type: 'PROVISIONAL',
    iteration: 2,
    description: '正在撰写论文',
    extra: { stage: 'paper_writing' },
  };

  const presentation = presentRsiNode(
    node,
    context(
      'ARTIFACT',
      'PAPER',
      [
        {
          node_id: 'ROOT',
          iteration: 0,
          parent_id: null,
          type: 'ROOT',
          adopted: true,
          changes: [],
          extra: {},
        },
        node,
      ],
      true,
    ),
  );

  assert.equal(presentation.lifecycle, 'generating');
  assert.equal(presentation.stageLabel, '撰写中');
  assert.equal(presentation.runtimeKind, 'evaluating');
  assert.equal(presentation.runtimeLabel, '生成中');
});

test('paper score_overall is rendered and rejected reason is human-readable', () => {
  const parent = {
    node_id: 'paper-parent',
    iteration: 1,
    parent_id: 'ROOT',
    type: 'ADOPTED',
    adopted: true,
    score: null,
    description: '上一版论文',
    changes: [],
    extra: { paper: { round_index: 1, attempt: 1, outcome: 'success', score_overall: 0.82 } },
  };
  const rejected = {
    node_id: 'paper-rejected',
    iteration: 2,
    parent_id: 'paper-parent',
    type: 'REJECTED',
    adopted: false,
    score: null,
    description: '完成论文版本',
    failure_reason: 'score 0.79 did not exceed parent score 0.82',
    failure_class: 'rejected_by_score',
    changes: [{ group: 'paper', operation: 'modify', summary: '新增实验小节' }],
    extra: { paper: { round_index: 2, attempt: 1, outcome: 'rejected', score_overall: 0.79 } },
  };
  const presentation = presentRsiNode(rejected, context('ARTIFACT', 'PAPER', [parent, rejected]));
  assert.equal(presentation.title, '第 2 轮 · 论文候选');
  assert.equal(presentation.lifecycle, 'rejected');
  assert.equal(presentation.reasonLabel, '得分未超过父节点');
  assert.equal(presentation.score, 0.79);
  assert.equal(presentation.parentScore, 0.82);
  assert.deepEqual(nodeScoreLines(rejected)[0], { value: '0.8', label: '得分' });
});

test('parallel program candidates get attempt numbering without exposing provider ids', () => {
  const nodes = [
    {
      node_id: 'artifact:task:root',
      iteration: 0,
      parent_id: null,
      type: 'ROOT',
      adopted: true,
      score: 0.5,
      changes: [],
      extra: { program: {} },
    },
    {
      node_id: 'artifact:task:attempt:1:a',
      iteration: 1,
      parent_id: 'ROOT',
      type: 'PROVISIONAL',
      adopted: false,
      score: null,
      changes: [],
      extra: { program: { logical_kind: 'pending' } },
    },
    {
      node_id: 'artifact:task:attempt:1:b',
      iteration: 1,
      parent_id: 'ROOT',
      type: 'PROVISIONAL',
      adopted: false,
      score: null,
      changes: [],
      extra: { program: { logical_kind: 'pending' } },
    },
    {
      node_id: 'artifact:task:attempt:1:c',
      iteration: 1,
      parent_id: 'ROOT',
      type: 'PROVISIONAL',
      adopted: false,
      score: null,
      changes: [],
      extra: { program: { logical_kind: 'pending' } },
    },
  ];
  const presentation = presentRsiNode(nodes[1], context('ARTIFACT', 'PROGRAM', nodes, true));
  assert.match(presentation.title, /^第 1 轮 · 程序候选 [1-3]\/3$/);
  assert.equal(presentation.attempt.total, 3);
  assert.equal(presentation.lifecycle, 'generating');
  assert.equal(nodeChangeDisplayLabel({ group: 'program', operation: 'modify' }), '程序逻辑 · 调整');
});

test('runtime failures are separated from score-based rejection', () => {
  const failed = {
    node_id: 'paper-failed',
    iteration: 3,
    parent_id: 'ROOT',
    type: 'REJECTED',
    adopted: false,
    score: null,
    description: null,
    failure_reason: 'manager decision failed after 3 attempts',
    failure_class: 'pipeline_failed',
    changes: [],
    extra: { paper: { round_index: 3, attempt: 1, outcome: 'failed' } },
  };
  const presentation = presentRsiNode(failed, context('ARTIFACT', 'PAPER', [failed], false));
  assert.equal(presentation.title, '第 3 轮 · 论文尝试');
  assert.equal(presentation.lifecycle, 'failed');
  assert.equal(presentation.reasonLabel, '管理器决策失败');
});
