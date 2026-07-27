import assert from 'node:assert/strict';
import test from 'node:test';

import {
  SESSION_CREATE_METADATA_POLL_ATTEMPTS,
  SESSION_CREATE_METADATA_POLL_INTERVAL_MS,
  SESSION_CREATE_TIMEOUT_MS,
  createConversationSession,
  isAlreadyExistsError,
  isRequestTimeoutError,
  resolveCreatedSessionId,
} from '../node_modules/.cache/create-conversation-session/multi-session/state/createConversationSession.js';

const fastRecoverOptions = {
  metadataPollAttempts: 3,
  metadataPollIntervalMs: 0,
  sleep: async () => {},
};

test('SESSION_CREATE_TIMEOUT_MS is longer than the default 15s RPC timeout', () => {
  assert.equal(SESSION_CREATE_TIMEOUT_MS, 60_000);
  assert.equal(SESSION_CREATE_METADATA_POLL_ATTEMPTS, 5);
  assert.equal(SESSION_CREATE_METADATA_POLL_INTERVAL_MS, 500);
});

test('resolveCreatedSessionId accepts snake_case and camelCase', () => {
  assert.equal(resolveCreatedSessionId({ session_id: 'sess_a' }), 'sess_a');
  assert.equal(resolveCreatedSessionId({ sessionId: 'sess_b' }), 'sess_b');
  assert.equal(resolveCreatedSessionId({}), undefined);
});

test('isRequestTimeoutError / isAlreadyExistsError read error.code', () => {
  assert.equal(isRequestTimeoutError({ code: 'REQUEST_TIMEOUT' }), true);
  assert.equal(isRequestTimeoutError({ code: 'WS_NOT_READY' }), false);
  assert.equal(isAlreadyExistsError({ code: 'ALREADY_EXISTS' }), true);
  assert.equal(isAlreadyExistsError(new Error('x')), false);
});

test('createConversationSession succeeds on first create', async () => {
  const calls = [];
  const request = async (method, params, options) => {
    calls.push({ method, params, options });
    return { sessionId: 'sess_new', projectId: 'default', workMode: 'work' };
  };

  const created = await createConversationSession(
    request,
    { session_id: 'sess_new', mode: 'agent' },
    'sess_new',
  );

  assert.deepEqual(created, {
    session_id: 'sess_new',
    project_id: 'default',
    project_dir: undefined,
    work_mode: 'work',
  });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, 'session.create');
  assert.equal(calls[0].options.timeoutMs, SESSION_CREATE_TIMEOUT_MS);
});

test('createConversationSession recovers via get_metadata after REQUEST_TIMEOUT', async () => {
  const calls = [];
  const request = async (method, params, options) => {
    calls.push({ method, params, options });
    if (method === 'session.create') {
      const err = new Error('timeout');
      err.code = 'REQUEST_TIMEOUT';
      throw err;
    }
    if (method === 'session.get_metadata') {
      return { session_id: 'sess_recover', project_id: 'default', work_mode: 'work' };
    }
    throw new Error(`unexpected method ${method}`);
  };

  const created = await createConversationSession(
    request,
    { session_id: 'sess_recover', mode: 'agent' },
    'sess_recover',
    fastRecoverOptions,
  );

  assert.equal(created.session_id, 'sess_recover');
  assert.deepEqual(
    calls.map((c) => c.method),
    ['session.create', 'session.get_metadata'],
  );
});

test('createConversationSession polls metadata before retrying create', async () => {
  const calls = [];
  let metaCount = 0;
  const request = async (method) => {
    calls.push(method);
    if (method === 'session.create') {
      const err = new Error('timeout');
      err.code = 'REQUEST_TIMEOUT';
      throw err;
    }
    if (method === 'session.get_metadata') {
      metaCount += 1;
      if (metaCount < 3) {
        const missing = new Error('not found');
        missing.code = 'NOT_FOUND';
        throw missing;
      }
      return { session_id: 'sess_poll', project_id: 'default', work_mode: 'work' };
    }
    throw new Error(`unexpected method ${method}`);
  };

  const created = await createConversationSession(
    request,
    { session_id: 'sess_poll', mode: 'agent' },
    'sess_poll',
    fastRecoverOptions,
  );

  assert.equal(created.session_id, 'sess_poll');
  assert.deepEqual(calls, [
    'session.create',
    'session.get_metadata',
    'session.get_metadata',
    'session.get_metadata',
  ]);
});

test('createConversationSession retries create and treats ALREADY_EXISTS as success', async () => {
  const calls = [];
  let createCount = 0;
  const request = async (method, params, options) => {
    calls.push({ method, params, options });
    if (method === 'session.create') {
      createCount += 1;
      if (createCount === 1) {
        const err = new Error('timeout');
        err.code = 'REQUEST_TIMEOUT';
        throw err;
      }
      const exists = new Error('session already exists');
      exists.code = 'ALREADY_EXISTS';
      throw exists;
    }
    if (method === 'session.get_metadata') {
      const missing = new Error('not found');
      missing.code = 'NOT_FOUND';
      throw missing;
    }
    throw new Error(`unexpected method ${method}`);
  };

  const created = await createConversationSession(
    request,
    { session_id: 'sess_exists', mode: 'agent' },
    'sess_exists',
    fastRecoverOptions,
  );

  assert.equal(created.session_id, 'sess_exists');
  assert.deepEqual(
    calls.map((c) => c.method),
    [
      'session.create',
      'session.get_metadata',
      'session.get_metadata',
      'session.get_metadata',
      'session.create',
      'session.get_metadata',
    ],
  );
});

test('non-timeout errors are not swallowed', async () => {
  const request = async () => {
    const err = new Error('bad request');
    err.code = 'BAD_REQUEST';
    throw err;
  };

  await assert.rejects(
    () => createConversationSession(request, { session_id: 'sess_x' }, 'sess_x'),
    (error) => error.code === 'BAD_REQUEST',
  );
});
