import assert from 'node:assert/strict';
import test from 'node:test';
import {
  normalizeA2AOutboundAgent,
  normalizeA2AOutboundDiscovery,
  normalizeA2AOutboundList,
  normalizeA2AOutboundSettings,
  shouldAcceptA2AOutboundResponse,
} from '../node_modules/.cache/a2a-outbound-panel-state/components/A2AIngressPanel/a2aOutboundPanelState.js';

const selectedInterface = {
  protocol_binding: 'JSONRPC',
  protocol_version: '1.0.0',
  url: 'https://agent.example.com/a2a',
};

const agent = {
  agent_id: 'agent-1',
  display_name: 'Research Agent',
  card_revision: 1,
  agent_card: { name: 'Research Agent' },
  selected_interface: selectedInterface,
  enabled: true,
  availability: 'available',
  has_credential: false,
  connect_timeout_seconds: 10,
  sync_wait_seconds: 300,
  last_checked_at: null,
  last_error_summary: null,
  pending_revision: null,
};

test('A2A outbound discovery requires an id, name, and compatible interface', () => {
  const discovery = normalizeA2AOutboundDiscovery({
    discovery_id: 'disc-1',
    expires_at: '2026-08-26T12:00:00Z',
    source_url: 'https://agent.example.com',
    card_path: '/.well-known/agent-card.json',
    card_fingerprint: 'sha256:card',
    agent: {
      name: 'Research Agent',
      description: 'Researches',
      version: '1.0.0',
      skills: [],
      compatible_interfaces: [selectedInterface],
    },
    security_requirements: [],
    warnings: [],
  });

  assert.ok(discovery);
  assert.equal(discovery.agent.compatible_interfaces[0].url, selectedInterface.url);
  assert.equal(normalizeA2AOutboundDiscovery({ discovery_id: 'disc-1', agent: { name: 'Agent' } }), null);
});

test('A2A outbound agent rejects unknown availability and malformed interfaces', () => {
  assert.ok(normalizeA2AOutboundAgent(agent));
  assert.equal(normalizeA2AOutboundAgent({ ...agent, availability: 'working' }), null);
  assert.equal(normalizeA2AOutboundAgent({ ...agent, selected_interface: { url: selectedInterface.url } }), null);
});

test('A2A outbound list rejects any malformed item', () => {
  assert.deepEqual(normalizeA2AOutboundList({ items: [agent] }), [agent]);
  assert.equal(normalizeA2AOutboundList({ items: [agent, { agent_id: '' }] }), null);
  assert.equal(normalizeA2AOutboundList({ items: 'invalid' }), null);
});

test('A2A outbound settings require an explicit loopback boolean', () => {
  assert.deepEqual(normalizeA2AOutboundSettings({ allow_loopback_http: true }), { allow_loopback_http: true });
  assert.equal(normalizeA2AOutboundSettings({ allow_loopback_http: 'true' }), null);
  assert.equal(normalizeA2AOutboundSettings({}), null);
});

test('A2A outbound ignores obsolete responses', () => {
  assert.equal(shouldAcceptA2AOutboundResponse(7, 7), true);
  assert.equal(shouldAcceptA2AOutboundResponse(6, 7), false);
});
