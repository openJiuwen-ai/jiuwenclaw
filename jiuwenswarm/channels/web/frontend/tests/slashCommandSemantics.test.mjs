import assert from 'node:assert/strict';
import test from 'node:test';

import { shouldExecuteRegisteredSlashCommand } from '../node_modules/.cache/slash-command-semantics/components/ChatPanel/slashCommands/semantics.js';

test('standalone plan command executes', () => {
  assert.equal(shouldExecuteRegisteredSlashCommand('plan', ''), true);
  assert.equal(shouldExecuteRegisteredSlashCommand('PLAN', '   '), true);
});

test('plan with arguments remains an ordinary chat message', () => {
  assert.equal(shouldExecuteRegisteredSlashCommand('plan', 'hi'), false);
  assert.equal(shouldExecuteRegisteredSlashCommand('plan', 'open'), false);
});

test('other registered slash commands keep their existing argument behavior', () => {
  assert.equal(shouldExecuteRegisteredSlashCommand('btw', '介绍一下南京'), true);
  assert.equal(shouldExecuteRegisteredSlashCommand('compact', ''), true);
});
