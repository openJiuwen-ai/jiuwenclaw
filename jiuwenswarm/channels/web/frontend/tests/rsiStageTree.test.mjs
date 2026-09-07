import assert from 'node:assert/strict';
import test from 'node:test';
import { useRsiStore } from '../node_modules/.cache/rsi-stage/rsiStore.mjs';

test('program progress counts completed candidates despite out-of-order completion', () => {
  const extra = { program: {} };
  const root = { node_id: 'ROOT', parent_id: null, iteration: 0, type: 'ROOT', extra };
  useRsiStore.setState({ detail: { p: { tree: { nodes: [root], depth: 0, iteration: 0 }, pendingTreeNodes: [] } } });
  const push = (node) => useRsiStore.getState().applyTreeDelta({ task_id: 'p', nodes: [{ ...node, extra }] });
  push({ node_id: 'second', parent_id: 'ROOT', iteration: 2, type: 'ADOPTED' });
  assert.equal(useRsiStore.getState().detail.p.tree.iteration, 1);
  push({ node_id: 'first', parent_id: 'ROOT', iteration: 1, type: 'PROVISIONAL' });
  assert.equal(useRsiStore.getState().detail.p.tree.iteration, 1);
  push({ node_id: 'first', parent_id: 'ROOT', iteration: 1, type: 'REJECTED' });
  assert.equal(useRsiStore.getState().detail.p.tree.iteration, 2);
});

test('stages update one node; completed rounds and branch depth remain distinct', () => {
  const root = { node_id: 'ROOT', parent_id: null, iteration: 0, type: 'ROOT' };
  useRsiStore.setState({ detail: { t: { tree: { nodes: [root], depth: 0, iteration: 0 }, pendingTreeNodes: [] } } });
  const push = (node) => useRsiStore.getState().applyTreeDelta({ task_id: 't', nodes: [node] });
  const tree = () => useRsiStore.getState().detail.t.tree;
  const first = { node_id: 'n1', parent_id: 'ROOT', iteration: 1, type: 'PROVISIONAL' };
  push({ ...first, description: '正在调研文献' });
  push({ ...first, description: '正在执行实验' });
  assert.equal(tree().nodes.length, 2);
  assert.equal(tree().nodes[1].description, '正在执行实验');
  assert.equal(tree().iteration, 0);
  push({ ...first, type: 'REJECTED' });
  assert.equal(tree().iteration, 1);
  push({ node_id: 'n2', parent_id: 'ROOT', iteration: 2, type: 'PROVISIONAL' });
  assert.equal(tree().iteration, 1);
  assert.equal(tree().depth, 1);
  push({ node_id: 'n2', parent_id: 'ROOT', iteration: 2, type: 'ADOPTED' });
  assert.equal(tree().iteration, 2);
  assert.equal(tree().depth, 1);
});
