import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

const values = new Map();
globalThis.localStorage = {
  getItem(key) {
    return values.get(key) ?? null;
  },
  removeItem(key) {
    values.delete(key);
  },
  setItem(key, value) {
    values.set(key, String(value));
  },
};
globalThis.window = {
  __JIUWEN_USER_WEB_MODE__: 'personal',
  history: { replaceState() {} },
  location: { pathname: '/', search: '', replace() {} },
};

const { EnterpriseEntry, chooseAgent, orderedContextCandidates } = await import('../node_modules/.cache/user-web-entry/EnterpriseEntry.mjs');

function renderEntry(mode) {
  window.__JIUWEN_USER_WEB_MODE__ = mode;
  return renderToStaticMarkup(React.createElement(EnterpriseEntry, null, React.createElement('div', { id: 'user-web-content' }, 'user web content')));
}

function resetBrowserState() {
  values.clear();
  window.location.search = '';
}

test('personal mode renders the standalone User Web without enterprise authentication', () => {
  resetBrowserState();
  const html = renderEntry('personal');

  assert.match(html, /user web content/);
  assert.doesNotMatch(html, /ENTERPRISE WORKSPACE/);
});

test('enterprise mode redirects unauthenticated users instead of rendering User Web', () => {
  resetBrowserState();
  const html = renderEntry('enterprise');

  assert.match(html, /ENTERPRISE WORKSPACE/);
  assert.match(html, /正在前往登录页/);
  assert.doesNotMatch(html, /user web content/);
});

test('enterprise mode loads and validates an authorized context before rendering User Web', () => {
  resetBrowserState();
  localStorage.setItem('openjiuwen_access_token', 'manager-token');
  window.location.search = '?user_id=user-1&group_id=group-1&bot_id=bot-1&gateway_id=gateway-1';

  const html = renderEntry('enterprise');

  assert.match(html, /正在加载工作空间/);
  assert.doesNotMatch(html, /user web content/);
});

test('context candidates prefer URL values but retain every authorized combination', () => {
  const gateways = [
    { jiuwenclaw_id: 'gateway-1', jiuwenclaw_name: 'Gateway 1', gateway_endpoint: null },
    { jiuwenclaw_id: 'gateway-2', jiuwenclaw_name: 'Gateway 2', gateway_endpoint: null },
  ];
  const orgs = [
    { group_id: 'group-1', name: 'Group 1' },
    { group_id: 'group-2', name: 'Group 2' },
  ];

  const candidates = orderedContextCandidates(gateways, orgs, 'gateway-2', 'group-2');

  assert.deepEqual(
    candidates.map(({ gateway, org }) => [gateway.jiuwenclaw_id, org.group_id]),
    [
      ['gateway-2', 'group-2'],
      ['gateway-2', 'group-1'],
      ['gateway-1', 'group-2'],
      ['gateway-1', 'group-1'],
    ],
  );
});

test('agent selection accepts a still-authorized URL agent and otherwise falls back to the first agent', () => {
  const agents = [
    { template_id: 'template-1', template_name: 'Agent 1', resource_id: 'agent-1' },
    { template_id: 'template-2', template_name: 'Agent 2', resource_id: 'agent-2' },
  ];

  assert.equal(chooseAgent(agents, 'agent-2')?.resource_id, 'agent-2');
  assert.equal(chooseAgent(agents, 'removed-agent')?.resource_id, 'agent-1');
  assert.equal(chooseAgent([], 'agent-2'), null);
});
