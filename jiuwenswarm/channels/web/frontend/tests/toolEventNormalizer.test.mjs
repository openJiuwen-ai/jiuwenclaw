import assert from 'node:assert/strict';
import test from 'node:test';

import {
  normalizeToolResultPayload,
  plannedGraphToMermaid,
} from '../node_modules/.cache/tool-event-normalizer/toolEventNormalizer.js';

function payload(nodes, edges = []) {
  return {
    planned_graph: {
      graph: {
        id: 'plan-1',
        type: 'planned_graph',
        directed: true,
        metadata: { status: 'ready' },
        nodes: Object.fromEntries(nodes.map(id => [id, { label: id, metadata: { type: 'skill' } }])),
        edges,
      },
    },
  };
}

const plannedGraphInit = `%%{init: ${JSON.stringify({
  fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, monospace',
  themeVariables: { fontSize: '11px', radius: 8 },
})}}%%`;

test('projects a single node with its capability ID as Mermaid ID and label', () => {
  assert.equal(
    plannedGraphToMermaid(payload(['data-reconciliation'])),
    `${plannedGraphInit}\nflowchart LR\ndata-reconciliation("data-reconciliation")`,
  );
});

test('sorts nodes and edges deterministically for chains and branches', () => {
  const graph = payload(
    ['final_step', 'middle-step', 'first_step'],
    [
      { source: 'middle-step', target: 'final_step', relation: 'can_feed' },
      { source: 'first_step', target: 'middle-step', relation: 'can_feed' },
      { source: 'first_step', target: 'final_step', relation: 'can_feed' },
    ],
  );

  assert.equal(
    plannedGraphToMermaid(graph),
    [
      plannedGraphInit,
      'flowchart LR',
      'final_step("final_step")',
      'first_step("first_step")',
      'middle-step("middle-step")',
      'first_step --> final_step',
      'first_step --> middle-step',
      'middle-step --> final_step',
    ].join('\n'),
  );
});

test('accepts digits, underscores, and hyphens without aliases', () => {
  const mermaid = plannedGraphToMermaid(payload(['123_start', 'middle_step', 'final-step']));
  assert.match(mermaid, /123_start\("123_start"\)/);
  assert.match(mermaid, /middle_step\("middle_step"\)/);
  assert.match(mermaid, /final-step\("final-step"\)/);
  assert.doesNotMatch(mermaid, /n\d+/);
});

test('falls back when the graph is empty or structurally invalid', () => {
  assert.equal(plannedGraphToMermaid(payload([])), undefined);
  assert.equal(plannedGraphToMermaid({ planned_graph: { graph: { nodes: {}, edges: [] } } }), undefined);
  assert.equal(plannedGraphToMermaid(payload(['valid_id', 'bad.id'])), undefined);
  assert.equal(plannedGraphToMermaid(payload(['end'])), undefined);
  assert.equal(plannedGraphToMermaid(payload(['style'])), undefined);
  assert.equal(
    plannedGraphToMermaid({
      planned_graph: {
        graph: {
          nodes: { first: null, second: {} },
          edges: [],
        },
      },
    }),
    undefined,
  );
  assert.equal(
    plannedGraphToMermaid(payload(['first', 'second'], [
      { source: 'first', target: 'missing', relation: 'can_feed' },
    ])),
    undefined,
  );
  assert.equal(
    plannedGraphToMermaid(payload(['first', 'second'], [
      { source: 'first', target: 'second', relation: 'depends_on' },
    ])),
    undefined,
  );
});

test('only successful symphony_compose_graph results receive Mermaid', () => {
  const rawOutput = payload(['writer']);
  const composeResult = normalizeToolResultPayload({
    tool_name: 'symphony_compose_graph',
    success: true,
    raw_output: rawOutput,
    result: 'original result',
  });
  assert.equal(
    composeResult.mermaid,
    `${plannedGraphInit}\nflowchart LR\nwriter("writer")`,
  );

  const otherToolResult = normalizeToolResultPayload({
    tool_name: 'other_tool',
    success: true,
    raw_output: rawOutput,
  });
  assert.equal(otherToolResult.mermaid, undefined);

  const failedComposeResult = normalizeToolResultPayload({
    tool_name: 'symphony_compose_graph',
    success: false,
    raw_output: rawOutput,
  });
  assert.equal(failedComposeResult.mermaid, undefined);
});
