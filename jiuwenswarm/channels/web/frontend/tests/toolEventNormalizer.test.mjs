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
        nodes: Object.fromEntries(
          nodes.map(id => [id, { label: id, metadata: { type: 'skill' } }]),
        ),
        edges,
      },
    },
  };
}

const plannedGraphInit = `%%{init: ${JSON.stringify({
  fontFamily: 'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Monaco, Consolas, monospace',
  themeVariables: { fontSize: '11px', radius: 8 },
})}}%%`;

test('preserves Mermaid-safe capability IDs as node IDs and labels', () => {
  assert.equal(
    plannedGraphToMermaid(payload(['123_start', 'middle_step', 'final-step'])),
    [
      plannedGraphInit,
      'flowchart LR',
      '123_start("123_start")',
      'final-step("final-step")',
      'middle_step("middle_step")',
    ].join('\n'),
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

test('aliases Unicode and Mermaid-conflicting IDs without losing labels or edges', () => {
  assert.equal(
    plannedGraphToMermaid(
      payload(
        ['capability_0', 'class-foo', 'foo--bar', 'ppt大师', '交付总监'],
        [
          { source: 'class-foo', target: 'foo--bar', relation: 'can_feed' },
          { source: 'foo--bar', target: 'ppt大师', relation: 'can_feed' },
          { source: 'ppt大师', target: '交付总监', relation: 'can_feed' },
          { source: '交付总监', target: 'capability_0', relation: 'can_feed' },
        ],
      ),
    ),
    [
      plannedGraphInit,
      'flowchart LR',
      'capability_0("capability_0")',
      'capability_1("class-foo")',
      'capability_2("foo--bar")',
      'capability_3("ppt大师")',
      'capability_4("交付总监")',
      'capability_1 --> capability_2',
      'capability_2 --> capability_3',
      'capability_3 --> capability_4',
      'capability_4 --> capability_0',
    ].join('\n'),
  );
});

test('falls back when the graph is empty or structurally invalid', () => {
  const invalidGraphs = [
    payload([]),
    { planned_graph: { graph: { nodes: {}, edges: [] } } },
    payload(['valid_id', 'bad.id']),
    payload(['end']),
    payload(['style']),
    {
      planned_graph: {
        graph: { nodes: { first: null, second: {} }, edges: [] },
      },
    },
    payload(['first', 'second'], [
      { source: 'first', target: 'missing', relation: 'can_feed' },
    ]),
    payload(['first', 'second'], [
      { source: 'first', target: 'second', relation: 'depends_on' },
    ]),
  ];
  for (const graph of invalidGraphs) {
    assert.equal(plannedGraphToMermaid(graph), undefined);
  }
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

  for (const result of [
    { tool_name: 'other_tool', success: true, raw_output: rawOutput },
    { tool_name: 'symphony_compose_graph', success: false, raw_output: rawOutput },
  ]) {
    assert.equal(normalizeToolResultPayload(result).mermaid, undefined);
  }
});
