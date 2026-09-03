import assert from 'node:assert/strict';
import test from 'node:test';

import {
  getWebSlashCommandsForMode,
  shouldExecuteRegisteredSlashCommand,
  supportsWebSlashCommands,
} from '../node_modules/.cache/slash-command-semantics/components/ChatPanel/slashCommands/semantics.js';

test('standalone plan command executes', () => {
  assert.equal(shouldExecuteRegisteredSlashCommand('plan', '', 'agent'), true);
  assert.equal(shouldExecuteRegisteredSlashCommand('PLAN', '   ', 'agent'), true);
});

test('plan with arguments remains an ordinary chat message', () => {
  assert.equal(shouldExecuteRegisteredSlashCommand('plan', 'hi', 'agent'), false);
  assert.equal(shouldExecuteRegisteredSlashCommand('plan', 'open', 'agent'), false);
});

test('other registered slash commands keep their existing argument behavior', () => {
  assert.equal(shouldExecuteRegisteredSlashCommand('btw', '介绍一下南京', 'agent'), true);
  assert.equal(shouldExecuteRegisteredSlashCommand('compact', '', 'agent'), true);
});

test('team mode neither exposes nor executes web slash commands', () => {
  const commands = [{ name: 'btw' }, { name: 'compact' }, { name: 'plan' }];

  assert.equal(supportsWebSlashCommands('team'), false);
  assert.deepEqual(getWebSlashCommandsForMode(commands, 'team'), []);
  assert.equal(shouldExecuteRegisteredSlashCommand('btw', '介绍一下南京', 'team'), false);
  assert.equal(shouldExecuteRegisteredSlashCommand('compact', '', 'team'), false);
  assert.equal(shouldExecuteRegisteredSlashCommand('plan', '', 'team'), false);
});

test('single-agent mode keeps command visibility and execution', () => {
  const commands = [{ name: 'btw' }, { name: 'compact' }];

  assert.equal(supportsWebSlashCommands('agent'), true);
  assert.equal(getWebSlashCommandsForMode(commands, 'agent'), commands);
  assert.equal(shouldExecuteRegisteredSlashCommand('btw', '问题', 'agent'), true);
  assert.equal(shouldExecuteRegisteredSlashCommand('compact', '', 'agent'), true);
});
