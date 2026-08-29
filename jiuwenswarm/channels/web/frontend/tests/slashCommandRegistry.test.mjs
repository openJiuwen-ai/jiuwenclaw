import assert from 'node:assert/strict';
import test from 'node:test';

import {
  findSlashCommand,
} from '../node_modules/.cache/slash-command-registry/slashCommands/registry.js';

const NEW_CONVERSATION_ID = 'new';

function createContext(sessionId, inputLine) {
  const messages = [];
  const submissions = [];
  return {
    messages,
    submissions,
    context: {
      sessionId,
      mode: 'agent',
      inputLine,
      addMessage: (_sessionId, message) => messages.push(message),
      submitMessage: (content) => submissions.push(content),
    },
  };
}

test('/persist is registered and delegates new-session creation to the existing submit path', async () => {
  const command = findSlashCommand('persist');
  assert.ok(command);
  assert.equal(command.requiresSession, false);

  const state = createContext(NEW_CONVERSATION_ID, '/persist 帮我跟进产品发布');
  await command.execute(state.context, '帮我跟进产品发布');

  assert.deepEqual(state.submissions, ['/persist 帮我跟进产品发布']);
  assert.deepEqual(state.messages, []);
});

test('/persist requires a task on the new-session page', async () => {
  const command = findSlashCommand('persist');
  assert.ok(command);

  const state = createContext(NEW_CONVERSATION_ID, '/persist');
  await command.execute(state.context, '');

  assert.deepEqual(state.submissions, []);
  assert.match(state.messages[0].commandOutput, /\/persist <任务>/);
});

test('/persist does not mutate an existing session', async () => {
  const command = findSlashCommand('persist');
  assert.ok(command);

  const state = createContext('existing-session', '/persist 新任务');
  await command.execute(state.context, '新任务');

  assert.deepEqual(state.submissions, []);
  assert.match(state.messages[0].commandOutput, /只能在创建新会话时开启/);
});
