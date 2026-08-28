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
  location: { pathname: '/', search: '' },
};

const { EnterpriseEntry } = await import('../node_modules/.cache/user-web-entry/EnterpriseEntry.mjs');

function renderEntry(mode) {
  window.__JIUWEN_USER_WEB_MODE__ = mode;
  return renderToStaticMarkup(React.createElement(EnterpriseEntry, null, React.createElement('div', { id: 'personal-content' }, 'personal content')));
}

function resetBrowserState() {
  values.clear();
  window.location.search = '';
}

test('personal mode renders the standalone User Web without enterprise authentication', () => {
  resetBrowserState();
  const html = renderEntry('personal');

  assert.match(html, /personal content/);
  assert.doesNotMatch(html, /ENTERPRISE WORKSPACE|请从 Manager Web 进入/);
});

test('enterprise mode rejects direct access without Manager Web context', () => {
  resetBrowserState();
  const html = renderEntry('enterprise');

  assert.match(html, /ENTERPRISE WORKSPACE/);
  assert.match(html, /请从 Manager Web 进入/);
  assert.doesNotMatch(html, /personal content/);
  assert.doesNotMatch(html, /用户名|密码|登录工作空间/);
});

test('enterprise mode renders the embedded User Web with Manager authentication and scope', () => {
  resetBrowserState();
  localStorage.setItem('openjiuwen_access_token', 'manager-token');
  window.location.search = '?user_id=user-1&group_id=group-1&bot_id=bot-1&gateway_id=gateway-1';

  const html = renderEntry('enterprise');

  assert.match(html, /personal content/);
  assert.doesNotMatch(html, /请从 Manager Web 进入/);
});
