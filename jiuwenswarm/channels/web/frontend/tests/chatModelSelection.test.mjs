import assert from 'node:assert/strict';
import test from 'node:test';

import {
  resolveChatModelSelection,
  resolveConfiguredModelName,
  useSessionStore,
} from '../node_modules/.cache/chat-model-selection/sessionStore.mjs';

const models = [
  {
    model_name: 'main-model',
    alias: '主对话默认模型',
    api_base: 'https://example.test/main',
    api_key: 'main-key',
    model_provider: 'OpenAI',
  },
  {
    model_name: 'single-agent-model',
    alias: '单Agent模型',
    api_base: 'https://example.test/agent',
    api_key: 'agent-key',
    model_provider: 'OpenAI',
  },
];

test('team mode ignores the single-agent selection and shows the main-chat default', () => {
  const selected = resolveChatModelSelection(
    models,
    '单Agent模型',
    '主对话默认模型',
    true,
  );

  assert.equal(selected?.model_name, 'main-model');
});

test('single-agent mode keeps the explicitly selected model', () => {
  const selected = resolveChatModelSelection(
    models,
    '单Agent模型',
    '主对话默认模型',
    false,
  );

  assert.equal(selected?.model_name, 'single-agent-model');
});

test('scheduled team tasks resolve the configured default alias to its model ID', () => {
  assert.equal(
    resolveConfiguredModelName(models, '主对话默认模型'),
    'main-model',
  );
});

test('an unavailable configured default does not silently select another model', () => {
  assert.equal(resolveConfiguredModelName(models, 'missing-model'), null);
});

test('team runtime is synchronized to the canonical default model ID', () => {
  const sessionId = 'team-default-model-test';
  const store = useSessionStore.getState();
  store.ensureRuntime(sessionId);
  store.setMode(sessionId, 'team');
  store.setSelectedModelName(sessionId, '单Agent模型');
  store.setAvailableModels(models, 'main-model');

  assert.equal(useSessionStore.getState().getRuntime(sessionId)?.selectedModelName, 'main-model');
  assert.equal(useSessionStore.getState().getEffectiveModelName(sessionId), 'main-model');

  useSessionStore.getState().removeRuntime(sessionId);
});
