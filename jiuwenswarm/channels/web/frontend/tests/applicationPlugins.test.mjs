import assert from 'node:assert/strict';
import test from 'node:test';

import { enabledApplicationPlugins, normalizeApplicationPluginManifest } from '../node_modules/.cache/application-plugins/manifest.js';
import {
  APPLICATION_PLUGIN_SECRET_MASK,
  applicationPluginSettingsToDraft,
  isApplicationPluginSettingVisible,
  serializeApplicationPluginDraft,
} from '../node_modules/.cache/application-plugins/configuration.js';

test('normalizes and orders application plugin contributions', () => {
  const plugins = normalizeApplicationPluginManifest({
    api_version: 1,
    plugins: [
      {
        plugin_id: 'later',
        plugin_version: '1.0.0',
        enabled: true,
        id: 'later-page',
        nav_key: 'app:later',
        title: 'Later',
        render_mode: 'iframe',
        position: 200,
      },
      {
        plugin_id: 'video-duplex',
        plugin_version: '1.0.0',
        enabled: true,
        id: 'video-live',
        nav_key: 'app:video-duplex',
        title: 'Full-duplex',
        render_mode: 'bundled',
        position: 75,
      },
    ],
  });

  assert.deepEqual(
    plugins.map(plugin => plugin.plugin_id),
    ['video-duplex', 'later'],
  );
  assert.equal(plugins[0].enabled, true);
});

test('rejects unsupported and malformed manifests', () => {
  assert.deepEqual(normalizeApplicationPluginManifest({ api_version: 2, plugins: [] }), []);
  assert.deepEqual(
    normalizeApplicationPluginManifest({
      api_version: 1,
      plugins: [{ plugin_id: 'missing-fields' }],
    }),
    [],
  );
});

test('keeps disabled plugins manageable while hiding their workspace navigation', () => {
  const plugins = normalizeApplicationPluginManifest({
    api_version: 1,
    plugins: [
      {
        plugin_id: 'video-duplex',
        plugin_version: '1.0.0',
        enabled: false,
        id: 'video-live',
        nav_key: 'app:video-duplex',
        title: 'Full-duplex',
        render_mode: 'bundled',
        position: 75,
      },
    ],
  });

  assert.equal(plugins.length, 1);
  assert.deepEqual(enabledApplicationPlugins(plugins), []);
});

test('keeps backend-only plugins manageable without adding navigation', () => {
  const plugins = normalizeApplicationPluginManifest({
    api_version: 1,
    plugins: [
      {
        plugin_id: 'backend-only',
        plugin_version: '1.0.0',
        enabled: true,
        id: 'backend-only:management',
        nav_key: 'app:backend-only',
        title: 'Backend only',
        render_mode: 'none',
        position: 1000,
      },
    ],
  });

  assert.equal(plugins.length, 1);
  assert.deepEqual(enabledApplicationPlugins(plugins), []);
});

test('serializes schema-driven settings and honors conditional visibility', () => {
  const properties = {
    provider: { type: 'string', default: 'local' },
    endpoint: { type: 'string', 'x-visible-when': { provider: 'remote' } },
    retries: { type: 'integer', default: 2 },
    enabled: { type: 'boolean', default: true },
  };
  const draft = applicationPluginSettingsToDraft({}, properties);

  assert.deepEqual(draft, {
    provider: 'local',
    endpoint: '',
    retries: '2',
    enabled: true,
  });
  assert.equal(isApplicationPluginSettingVisible(properties.endpoint, draft), false);
  assert.deepEqual(serializeApplicationPluginDraft(draft, properties), {
    provider: 'local',
    endpoint: '',
    retries: 2,
    enabled: true,
  });
});

test('rejects empty numeric and malformed JSON settings before saving', () => {
  assert.throws(() => serializeApplicationPluginDraft({ timeout: '' }, { timeout: { type: 'number' } }), /不能为空/);
  assert.throws(() => serializeApplicationPluginDraft({ headers: '{' }, { headers: { type: 'object' } }), /有效的 JSON/);
});

test('shows configured secrets as a fixed mask and preserves them on save', () => {
  const properties = {
    api_key: { type: 'string', secret: true },
    endpoint: { type: 'string' },
  };
  const draft = applicationPluginSettingsToDraft({ api_key: '', endpoint: 'https://example.test' }, properties, ['api_key']);

  assert.equal(draft.api_key, APPLICATION_PLUGIN_SECRET_MASK);
  assert.deepEqual(serializeApplicationPluginDraft(draft, properties, ['api_key']), { api_key: '', endpoint: 'https://example.test' });
  assert.deepEqual(serializeApplicationPluginDraft({ ...draft, api_key: 'replacement' }, properties, ['api_key']), {
    api_key: 'replacement',
    endpoint: 'https://example.test',
  });
});
