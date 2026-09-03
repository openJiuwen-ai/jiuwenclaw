import assert from 'node:assert/strict';
import test from 'node:test';

import { buildSkillVersionOptions } from '../node_modules/.cache/skill-version-options/components/SkillPanel/skillVersionOptions.js';

const labels = { defaultSuffix: ' (default)', unavailableSuffix: ' (unavailable)' };

test('marks unavailable versions as disabled so they cannot be selected', () => {
  const options = buildSkillVersionOptions([
    { version: 'v3', is_default: true, available: true },
    { version: 'v2', is_default: false, available: false },
    { version: 'v1', is_default: false, available: true },
  ], labels);

  assert.deepEqual(options.map((o) => [o.version, o.disabled]), [
    ['v3', false],
    ['v2', true],
    ['v1', false],
  ]);
});

test('keeps unavailable versions visible in the list with the unavailable suffix', () => {
  const options = buildSkillVersionOptions([
    { version: 'v2', is_default: false, available: false },
  ], labels);

  assert.equal(options.length, 1);
  assert.equal(options[0].label, 'v2 (unavailable)');
});

test('appends the default suffix only to the default version', () => {
  const options = buildSkillVersionOptions([
    { version: 'v2', is_default: true, available: true },
    { version: 'v1', is_default: false, available: true },
  ], labels);

  assert.equal(options[0].label, 'v2 (default)');
  assert.equal(options[1].label, 'v1');
});

test('treats a version that is both default and unavailable as disabled with both suffixes', () => {
  const options = buildSkillVersionOptions([
    { version: 'v1', is_default: true, available: false },
  ], labels);

  assert.equal(options[0].disabled, true);
  assert.equal(options[0].label, 'v1 (default) (unavailable)');
});

test('drops entries without a usable version string', () => {
  const options = buildSkillVersionOptions([
    { version: '', is_default: false, available: true },
    { version: '  ', is_default: false, available: true },
    { version: 'v1', is_default: false, available: true },
  ], labels);

  assert.deepEqual(options.map((o) => o.version), ['v1']);
});
