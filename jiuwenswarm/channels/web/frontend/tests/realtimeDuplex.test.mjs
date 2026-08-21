import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

import { RealtimeDuplexSession } from '../node_modules/.cache/realtime-duplex/realtimeDuplex.mjs';

function createSession(videoFrame = null) {
  const states = [];
  const posted = [];
  const dispatchedToolResults = [];
  const assistantTexts = [];
  const diagnostics = [];
  const session = new RealtimeDuplexSession(
    { url: 'ws://example.test/realtime', model: 'test-model', refAudio: '' },
    {
      getVideoFrame: () => videoFrame,
      onAssistantText: (text, final, toolJobId, turnId) => (
        assistantTexts.push({ text, final, toolJobId, turnId })
      ),
      onUserActivity: () => undefined,
      onTurnAudio: () => undefined,
      onState: (state) => states.push(state),
      onError: () => undefined,
      onToolResultDispatched: (jobId) => dispatchedToolResults.push(jobId),
      onDiagnostic: (event) => diagnostics.push(event),
    },
  );
  session.playbackNode = { port: { postMessage: (message) => posted.push(message) } };
  const sent = [];
  session.socket = { readyState: 1, send: (message) => sent.push(JSON.parse(message)) };
  globalThis.WebSocket = { OPEN: 1 };
  return { session, states, posted, sent, dispatchedToolResults, assistantTexts, diagnostics };
}

test('base instructions forbid unsupported visual and external claims', () => {
  const { session } = createSession();

  assert.match(session.contextInstructions, /画面模糊.*不得猜测/);
  assert.match(session.contextInstructions, /异步工具结果.*不得给出实质结论/);
  assert.match(session.contextInstructions, /材料不足.*不得自行补齐结论/);
});

test('urgent playback preempts normal audio and then resumes it', () => {
  const workletSource = readFileSync(new URL('../public/duplex-playback.js', import.meta.url), 'utf8');
  const workletEvents = [];
  let PlaybackProcessor;
  class FakeAudioWorkletProcessor {
    constructor() {
      this.port = { onmessage: null, postMessage: (message) => workletEvents.push(message) };
    }
  }
  runInNewContext(workletSource, {
    AudioWorkletProcessor: FakeAudioWorkletProcessor,
    Int16Array,
    Math,
    sampleRate: 1_000,
    registerProcessor: (_name, processor) => { PlaybackProcessor = processor; },
  });
  const processor = new PlaybackProcessor();
  const send = (data) => processor.port.onmessage({ data });
  const render = () => {
    const output = new Float32Array(2);
    processor.process([], [[output]]);
    return Array.from(output).map((sample) => Math.round(sample * 32768));
  };

  send({ type: 'audio', lane: 'normal', pcm: new Int16Array([1000, 1000, 1000, 1000]).buffer });
  send({ type: 'drain', lane: 'normal' });
  assert.deepEqual(render(), [1000, 1000]);

  send({ type: 'audio', lane: 'urgent', pcm: new Int16Array([2000, 2000]).buffer });
  send({ type: 'drain', lane: 'urgent' });
  assert.deepEqual(render(), [2000, 2000]);
  assert.deepEqual(render(), [1000, 1000]);
  assert.deepEqual(workletEvents.map((event) => event.lane), ['urgent', 'normal']);
});

test('interrupt immediately clears active playback and ignores the old official response', () => {
  const { session, states, posted, sent } = createSession();
  session.assistantPlaying = true;
  session.responseActive = true;

  session.interruptForUserInput();

  assert.deepEqual(posted, [{ type: 'clear', cancelResponse: true }]);
  assert.equal(session.assistantPlaying, false);
  assert.equal(session.responseActive, false);
  assert.equal(session.discardOfficialResponseUntilListen, true);
  assert.deepEqual(states, ['listening']);
  assert.equal(sent.length, 1);
  assert.equal(sent[0].type, 'input_audio_buffer.append');
  assert.equal(sent[0].force_listen, true);
  assert.equal(sent[0].format, 'pcm16');
  assert.equal(sent[0].sample_rate_hz, 16_000);
});

test('a new text instruction is queued without waiting for the old response', async () => {
  const { session, posted, sent } = createSession();
  session.assistantPlaying = true;
  session.responseActive = true;

  const accepted = await session.sendTextTurn('停止监控');

  assert.match(accepted, /^text-/);
  assert.equal(session.pendingTextInputs.length, 1);
  assert.match(session.pendingTextInputs[0].text, /停止监控/);
  assert.deepEqual(posted, [{ type: 'clear', cancelResponse: true }]);
  assert.equal(sent[0].force_listen, true);
});

test('a completed tool result waits without interrupting an active response', () => {
  const { session, posted, sent, dispatchedToolResults } = createSession();
  session.assistantPlaying = true;
  session.responseActive = true;

  const accepted = session.enqueueToolResult({
    jobId: 'search-1',
    question: '介绍一下这家公司',
    result: 'Free search results for Luckin Coffee',
  });

  assert.equal(accepted, true);
  assert.deepEqual(posted, []);
  assert.deepEqual(sent, []);
  assert.deepEqual(dispatchedToolResults, []);

  session.sendAudio(new Int16Array(16_000), true);
  assert.equal(session.pendingToolResults.length, 1);
  assert.equal(sent[0].text, undefined);

  session.responseActive = false;
  session.assistantPlaying = false;
  session.lastInteractiveInputAt = Date.now() - 3_000;
  session.sendAudio(new Int16Array(16_000), true);

  assert.equal(session.pendingToolResults.length, 0);
  assert.match(sent[1].text, /介绍一下这家公司/);
  assert.match(sent[1].text, /Luckin Coffee/);
  assert.match(sent[1].text, /无法确认.*不得用常识、记忆或猜测补全/);
  assert.equal(sent[1].force_speak, true);
  assert.deepEqual(dispatchedToolResults, ['search-1']);
});

test('a tool result answer keeps its job id through the realtime response', () => {
  const { session, sent, assistantTexts, diagnostics } = createSession();
  session.enqueueToolResult({
    jobId: 'search-correlated',
    question: '这个品牌是什么',
    result: 'Luckin Coffee is a Chinese coffee chain.',
  });
  session.lastInteractiveInputAt = Date.now() - 3_000;

  session.sendAudio(new Int16Array(16_000), true);
  session.handleEvent({ type: 'response.output.delta', kind: 'text', text: '这是瑞幸咖啡。' });
  session.handleEvent({ type: 'response.listen' });

  assert.match(sent[0].text, /Luckin Coffee/);
  assert.deepEqual(assistantTexts.at(-1), {
    text: '这是瑞幸咖啡。',
    final: true,
    toolJobId: 'search-correlated',
    turnId: undefined,
  });
  assert.deepEqual(
    diagnostics.filter((event) => event.event.startsWith('search_result_')).map((event) => event.event),
    ['search_result_queued', 'search_result_dispatched', 'search_result_answered'],
  );
  assert.equal(
    diagnostics.find((event) => event.event === 'search_result_answered').realtime_answer,
    '这是瑞幸咖啡。',
  );
});

test('a voice turn keeps its id through the final realtime answer', () => {
  const { session, assistantTexts } = createSession();
  session.turnHasUserActivity = true;
  session.turnAudio = [new Int16Array(16_000)];
  session.turnSamples = 16_000;

  session.dispatchUserTurn('voice-turn-1');
  session.handleEvent({ type: 'response.created', response: { id: 'response-1' } });
  session.handleEvent({ type: 'response.output.delta', kind: 'text', text: '瓶身品牌是农夫山泉。' });
  session.handleEvent({ type: 'response.listen' });

  assert.deepEqual(assistantTexts.at(-1), {
    text: '瓶身品牌是农夫山泉。',
    final: true,
    toolJobId: undefined,
    turnId: 'voice-turn-1',
  });
});

test('a text turn returns the id used by its final realtime answer', async () => {
  const { session, assistantTexts } = createSession();
  const turnId = await session.sendTextTurn('搜索这个牌子的相关信息');

  session.sendAudio(new Int16Array(16_000), true);
  session.handleEvent({ type: 'response.created', response: { id: 'response-2' } });
  session.handleEvent({ type: 'response.output.delta', kind: 'text', text: '这是农夫山泉。' });
  session.handleEvent({ type: 'response.listen' });

  assert.equal(assistantTexts.at(-1).turnId, turnId);
  assert.equal(assistantTexts.at(-1).text, '这是农夫山泉。');
});

test('an interrupted tool answer is not attributed to the next response', () => {
  const { session, assistantTexts, diagnostics } = createSession();
  session.enqueueToolResult({ jobId: 'search-old', question: '旧问题', result: '旧结果' });
  session.lastInteractiveInputAt = Date.now() - 3_000;
  session.sendAudio(new Int16Array(16_000), true);

  session.responseActive = true;
  session.interruptForUserInput();
  session.discardOfficialResponseUntilListen = false;
  session.handleEvent({ type: 'response.output.delta', kind: 'text', text: '新的普通回答' });
  session.handleEvent({ type: 'response.listen' });

  assert.equal(assistantTexts.at(-1).toolJobId, undefined);
  assert.ok(diagnostics.some((event) => (
    event.event === 'search_result_response_interrupted' && event.job_id === 'search-old'
  )));
});

test('new user text has priority over a queued tool result', async () => {
  const { session, sent } = createSession();
  session.enqueueToolResult({
    jobId: 'search-2',
    question: '旧问题',
    result: '旧问题的搜索结果',
  });
  await session.sendTextTurn('新的用户问题');
  session.lastInteractiveInputAt = Date.now() - 3_000;

  session.sendAudio(new Int16Array(16_000), true);

  assert.match(sent[0].text, /新的用户问题/);
  assert.equal(session.pendingToolResults.length, 1);
  session.lastInteractiveInputAt = Date.now() - 3_000;
  session.sendAudio(new Int16Array(16_000), true);
  assert.match(sent[1].text, /旧问题的搜索结果/);
});

test('a task context deferred during a response is sent with the next audio frame', () => {
  const { session, sent } = createSession();
  session.sessionReady = true;
  session.responseActive = true;

  session.updateContext('continuously inspect the latest frame', []);
  assert.deepEqual(sent, []);

  session.responseActive = false;
  session.sendAudio(new Int16Array(16_000), true);

  assert.equal(sent.length, 2);
  assert.match(sent[0].session.instructions, /continuously inspect the latest frame/);
  assert.equal(sent[0].type, 'session.update');
  assert.equal(sent[1].type, 'input_audio_buffer.append');
});

test('changing a task updates context without replacing the realtime websocket', () => {
  const { session, sent } = createSession();
  const previousSocket = session.socket;
  session.sessionReady = true;

  session.updateContext('continuously translate new English text', []);

  assert.equal(session.socket, previousSocket);
  assert.equal(session.activeTask, 'continuously translate new English text');
  assert.equal(sent.length, 1);
  assert.match(sent[0].session.instructions, /continuously translate new English text/);
  assert.equal(sent[0].type, 'session.update');
});

test('an idle active task checks the latest frame without forcing a decision', () => {
  const frame = 'data:image/jpeg;base64,dGVzdA==';
  const { session, sent } = createSession(frame);
  session.sessionReady = true;
  session.updateContext('continuously inspect the latest frame', []);
  sent.length = 0;
  session.lastTaskReminderAt = Date.now() - 6_000;

  session.sendAudio(new Int16Array(16_000), true);

  assert.equal(sent.length, 1);
  assert.match(sent[0].text, /紧急当前任务检查.*continuously inspect the latest frame/s);
  assert.deepEqual(sent[0].video_frames, [frame]);
  assert.equal(sent[0].force_listen, undefined);
  assert.equal(sent[0].force_speak, undefined);
});

test('local playback tail does not block a reminder after the model returned to listen', () => {
  const { session, sent, diagnostics } = createSession();
  session.sessionReady = true;
  session.updateContext('continuously inspect the latest frame', []);
  sent.length = 0;
  session.lastTaskReminderAt = Date.now() - 6_000;
  session.responseActive = false;
  session.assistantPlaying = true;

  session.sendAudio(new Int16Array(16_000), true);

  assert.equal(sent.length, 1);
  assert.match(sent[0].text, /紧急当前任务检查.*continuously inspect the latest frame/s);
  assert.equal(sent[0].force_listen, undefined);
  assert.equal(sent[0].force_speak, undefined);
  assert.equal(session.pendingResponseLane, 'urgent');
  assert.equal(diagnostics.at(-1).source, 'urgent_current_task');
});

test('urgent current task check has priority over a queued search result', () => {
  const { session, sent } = createSession();
  session.sessionReady = true;
  session.updateContext('remind me immediately when I drink', []);
  session.enqueueToolResult({ jobId: 'search-waiting', question: 'brand info', result: 'grounded result' });
  sent.length = 0;
  session.lastTaskReminderAt = Date.now() - 6_000;

  session.sendAudio(new Int16Array(16_000), true);

  assert.match(sent[0].text, /紧急当前任务检查/);
  assert.equal(session.pendingToolResults.length, 1);
  assert.equal(session.pendingResponseLane, 'urgent');
});

test('audio from an urgent task response is tagged for urgent playback', async () => {
  const { session, posted } = createSession();
  session.activeTask = 'remind me immediately when I drink';
  session.hasActiveTask = true;
  session.pendingResponseLane = 'urgent';
  session.decodeOutputAudio = async () => new Int16Array([1, 2]);

  session.handleEvent({ type: 'response.created', response: { id: 'urgent-response' } });
  session.handleEvent({
    type: 'response.audio.delta',
    response_id: 'urgent-response',
    delta: 'ignored-by-test-decoder',
  });
  await Promise.resolve();

  const audio = posted.find((message) => message.type === 'audio');
  assert.equal(audio.lane, 'urgent');
  session.handleEvent({ type: 'response.done', response_id: 'urgent-response' });
  const drain = posted.find((message) => message.type === 'drain');
  assert.equal(drain.lane, 'urgent');
});

test('a short barge-in candidate pauses playback and resumes without cancelling', () => {
  const { session, posted, sent } = createSession();
  session.assistantPlaying = true;
  session.responseActive = true;

  session.startBargeInCandidate(1600, 1400);
  session.clearBargeInCandidate(true);

  assert.deepEqual(posted, [{ type: 'pause' }, { type: 'resume' }]);
  assert.equal(session.responseActive, true);
  assert.equal(session.assistantPlaying, true);
  assert.deepEqual(sent, []);
});

test('session.closed before session.created rejects startup with the backend reason', async () => {
  const diagnostics = [];
  let socket;
  class StartupSocket {
    static OPEN = 1;

    constructor() {
      socket = this;
      this.readyState = StartupSocket.OPEN;
    }

    close() {}
    send() {}
  }
  globalThis.window = globalThis;
  globalThis.WebSocket = StartupSocket;
  const session = new RealtimeDuplexSession(
    { url: 'ws://example.test/realtime', model: 'test-model', refAudio: '' },
    {
      getVideoFrame: () => null,
      onAssistantText: () => undefined,
      onUserActivity: () => undefined,
      onTurnAudio: () => undefined,
      onState: () => undefined,
      onError: () => undefined,
      onDiagnostic: (event) => diagnostics.push(event),
    },
  );

  const opening = session.openSocket();
  socket.onmessage({ data: JSON.stringify({ type: 'session.closed', reason: 'backend_error' }) });

  await assert.rejects(opening, /Realtime 会话初始化失败：backend_error/);
  assert.equal(diagnostics.at(-1).event, 'realtime_websocket_error');
  assert.equal(diagnostics.at(-1).message, 'backend_error');
});
