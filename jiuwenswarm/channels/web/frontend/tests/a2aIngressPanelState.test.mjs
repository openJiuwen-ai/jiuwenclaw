import assert from 'node:assert/strict';
import test from 'node:test';
import {
  canOperateA2AIngress,
  draftFromA2AIngressSnapshot,
  isA2AIngressTransitioning,
  normalizeA2AIngressHistory,
  normalizeA2AIngressSnapshot,
  shouldAcceptA2AIngressResponse,
  toA2AIngressPatch,
  validateA2AIngressDraft,
} from '../node_modules/.cache/a2a-ingress-panel-state/components/A2AIngressPanel/a2aIngressPanelState.js';

const snapshot = {
  enabled: true,
  state: 'running',
  desired_host: '127.0.0.1',
  desired_port: 19100,
  desired_rpc_path: '/a2a',
  desired_card_path: '/.well-known/agent-card.json',
  desired_extended_card_path: '/agent/authenticatedExtendedCard',
  desired_protocol_version: '1.0.0',
  desired_app_name: 'Test Agent',
  desired_app_description: 'Test description',
  desired_app_version: '1.2.3',
  desired_expose_reasoning: false,
  desired_rpc_url: 'http://127.0.0.1:19100/a2a',
  desired_card_url: 'http://127.0.0.1:19100/.well-known/agent-card.json',
  effective_host: '127.0.0.1',
  effective_port: 19100,
  effective_rpc_path: '/a2a',
  effective_card_path: '/.well-known/agent-card.json',
  effective_rpc_url: 'http://127.0.0.1:19100/a2a',
  effective_card_url: 'http://127.0.0.1:19100/.well-known/agent-card.json',
  exposure_warning: null,
  started_at: 1,
  last_error: null,
  config_revision: 2,
};

test('A2A ingress snapshot preserves desired configuration for the form', () => {
  const normalized = normalizeA2AIngressSnapshot(snapshot);
  assert.ok(normalized);
  assert.deepEqual(draftFromA2AIngressSnapshot(normalized), {
    host: '127.0.0.1',
    port: '19100',
    rpc_path: '/a2a',
    protocol_version: '1.0.0',
    card_path: '/.well-known/agent-card.json',
    extended_card_path: '/agent/authenticatedExtendedCard',
    app_name: 'Test Agent',
    app_description: 'Test description',
    app_version: '1.2.3',
    expose_reasoning: false,
  });
});

test('A2A ingress form validates locally and sends typed patch values', () => {
  const draft = draftFromA2AIngressSnapshot(normalizeA2AIngressSnapshot(snapshot));
  assert.equal(validateA2AIngressDraft({ ...draft, port: '0' }), 'port');
  assert.equal(validateA2AIngressDraft({ ...draft, rpc_path: 'a2a' }), 'rpc_path');
  assert.deepEqual(toA2AIngressPatch({ ...draft, host: ' 0.0.0.0 ', port: '19101' }), {
    host: '0.0.0.0',
    port: 19101,
    rpc_path: '/a2a',
    protocol_version: '1.0.0',
    card_path: '/.well-known/agent-card.json',
    extended_card_path: '/agent/authenticatedExtendedCard',
    app_name: 'Test Agent',
    app_description: 'Test description',
    app_version: '1.2.3',
    expose_reasoning: false,
  });
});

test('A2A ingress only polls while a lifecycle transition is in progress', () => {
  assert.equal(isA2AIngressTransitioning('starting'), true);
  assert.equal(isA2AIngressTransitioning('stopping'), true);
  assert.equal(isA2AIngressTransitioning('running'), false);
});

test('A2A ingress lifecycle actions wait for a snapshot and a saved form', () => {
  const normalized = normalizeA2AIngressSnapshot(snapshot);
  assert.equal(canOperateA2AIngress(null, true, false, false, 'enable'), false);
  assert.equal(canOperateA2AIngress(normalized, true, false, true, 'enable'), false);
  assert.equal(canOperateA2AIngress(normalized, true, false, false, 'enable'), false);
  assert.equal(canOperateA2AIngress(normalized, true, false, false, 'disable'), true);
});

test('A2A ingress ignores obsolete refresh responses', () => {
  assert.equal(shouldAcceptA2AIngressResponse(4, 4), true);
  assert.equal(shouldAcceptA2AIngressResponse(3, 4), false);
});

test('A2A ingress history accepts lifecycle metadata and rejects malformed rows', () => {
  assert.deepEqual(normalizeA2AIngressHistory({
    items: [{
      request_id: 'req-1', context_id: 'ctx-1', message_id: 'msg-1', operation: 'message',
      status: 'completed', started_at: 10, finished_at: 10.2, duration_ms: 200, error: null,
    }],
    total: 1,
  }), {
    items: [{
      request_id: 'req-1', context_id: 'ctx-1', message_id: 'msg-1', operation: 'message',
      status: 'completed', started_at: 10, finished_at: 10.2, duration_ms: 200, error: null,
    }],
    total: 1,
  });
  assert.equal(normalizeA2AIngressHistory({ items: [{ request_id: '', status: 'completed' }] }), null);
});
