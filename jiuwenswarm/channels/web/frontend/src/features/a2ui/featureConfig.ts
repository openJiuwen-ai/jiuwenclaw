// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

let a2uiGenerationEnabled = true;
let a2uiRenderingEnabled = true;

interface A2UIFeatureConfig {
  generationEnabled: boolean;
  renderingEnabled: boolean;
}

/** Set the independent frontend A2UI generation and rendering flags. */
export function setA2UIFeatureConfig(config: A2UIFeatureConfig) {
  a2uiGenerationEnabled = config.generationEnabled;
  a2uiRenderingEnabled = config.renderingEnabled;
}

/** Set both flags for compatibility with the legacy combined switch. */
export function setA2UIFeatureEnabled(enabled: boolean) {
  setA2UIFeatureConfig({
    generationEnabled: enabled,
    renderingEnabled: enabled,
  });
}

/** Return whether the frontend should parse and render A2UI content. */
export function isA2UIFeatureEnabled() {
  return isA2UIRenderingEnabled();
}

/** Return whether A2UI client actions may continue through the generation flow. */
export function isA2UIGenerationEnabled() {
  return a2uiGenerationEnabled;
}

/** Return whether the frontend should parse and render A2UI content. */
export function isA2UIRenderingEnabled() {
  return a2uiRenderingEnabled;
}

/** Return whether rendered A2UI controls must be read-only. */
export function shouldDisableA2UIInteraction(disableInteraction = false) {
  return disableInteraction || !a2uiGenerationEnabled;
}

/** Normalize config values sent over the WebSocket config RPC boundary. */
export function normalizeA2UIEnabled(value: unknown) {
  if (typeof value === 'boolean') {
    return value;
  }
  const text = String(value ?? 'true').trim().toLowerCase();
  return !['0', 'false', 'no', 'off'].includes(text);
}
