import assert from 'node:assert/strict';
import test from 'node:test';

import { parseA2UIContent } from '../node_modules/.cache/a2ui-feature-config/a2uiContent.js';
import {
  isA2UIGenerationEnabled,
  isA2UIRenderingEnabled,
  setA2UIFeatureConfig,
  shouldDisableA2UIInteraction,
} from '../node_modules/.cache/a2ui-feature-config/featureConfig.js';

const A2UI_CONTENT = '<a2ui-json>[{"beginRendering":{"surfaceId":"history","root":"root"}}]</a2ui-json>';

for (const scenario of [
  { generationEnabled: false, renderingEnabled: false },
  { generationEnabled: false, renderingEnabled: true },
  { generationEnabled: true, renderingEnabled: false },
  { generationEnabled: true, renderingEnabled: true },
]) {
  const name = `generation=${scenario.generationEnabled} rendering=${scenario.renderingEnabled}`;
  test(name, () => {
    setA2UIFeatureConfig(scenario);

    const parts = parseA2UIContent(A2UI_CONTENT, {
      enabled: isA2UIRenderingEnabled(),
    });

    assert.equal(isA2UIGenerationEnabled(), scenario.generationEnabled);
    assert.equal(parts[0]?.kind, scenario.renderingEnabled ? 'a2ui' : 'text');
    assert.equal(shouldDisableA2UIInteraction(), !scenario.generationEnabled);
  });
}

test('an existing interaction lock remains active', () => {
  setA2UIFeatureConfig({ generationEnabled: true, renderingEnabled: true });

  assert.equal(shouldDisableA2UIInteraction(true), true);
});
