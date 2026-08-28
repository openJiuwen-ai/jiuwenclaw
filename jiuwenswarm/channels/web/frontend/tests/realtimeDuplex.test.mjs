import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';

import { RealtimeDuplexSession } from '../node_modules/.cache/realtime-duplex/realtimeDuplex.mjs';

function createSession(videoFrame = null, dialect = 'minicpm') {
  const states = [];
  const posted = [];
  const dispatchedToolResults = [];
  const readyToolResults = [];
  const assistantTexts = [];
  const userTexts = [];
  const diagnostics = [];
  const functionCalls = [];
  const userTurnAudios = [];
  const session = new RealtimeDuplexSession(
    { url: 'ws://example.test/realtime', dialect, refAudio: '' },
    {
      getVideoFrame: () => videoFrame,
      onAssistantText: (text, final, toolJobId, turnId) => (
        assistantTexts.push({ text, final, toolJobId, turnId })
      ),
      onUserText: (text, final) => userTexts.push({ text, final }),
      onUserTurnStarted: () => undefined,
      onUserTurnAudio: (audio, turnId) => userTurnAudios.push({ audio, turnId }),
      onState: (state) => states.push(state),
      onError: () => undefined,
      onToolResultDispatched: (jobId) => dispatchedToolResults.push(jobId),
      onToolResultReady: (toolResult) => readyToolResults.push(toolResult),
      onFunctionCall: (call) => functionCalls.push(call),
      onDiagnostic: (event) => diagnostics.push(event),
    },
  );
  session.playbackNode = { port: { postMessage: (message) => posted.push(message) } };
  const sent = [];
  session.socket = { readyState: 1, send: (message) => sent.push(JSON.parse(message)) };
  session.sessionReady = true;
  globalThis.WebSocket = { OPEN: 1 };
  return {
    session, states, posted, sent, dispatchedToolResults, readyToolResults,
    assistantTexts, userTexts, diagnostics, functionCalls, userTurnAudios,
  };
}

test('base instructions forbid unsupported visual and external claims', () => {
  const { session } = createSession();

  assert.match(session.contextInstructions, /画面模糊.*不得猜测/);
  assert.match(session.contextInstructions, /收到\[异步工具结果\].*不得给出任何实质结论/);
  assert.match(session.contextInstructions, /我目前不知道，需要搜索确认/);
  assert.match(session.contextInstructions, /香港今天天气.*不得说晴、阴、雨/);
  assert.match(session.contextInstructions, /材料不足.*不得自行补齐结论/);
});

test('playback waits for the target 400ms startup buffer and drains short tails', () => {
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

  send({ type: 'audio', pcm: new Int16Array(400).fill(1000).buffer, responseId: 'response-1' });
  assert.deepEqual(render(), [0, 0]);
  for (let index = 0; index < 199; index += 1) render();
  assert.notDeepEqual(render(), [0, 0]);

  send({ type: 'drain', responseId: 'response-1' });
  for (let index = 0; index < 200; index += 1) render();
  assert.equal(workletEvents.at(-1).type, 'drained');
  assert.equal(workletEvents.at(-1).responseId, 'response-1');
});

test('MiniCPM user activity does not clear playback or send force-listen', () => {
  const { session, posted, sent } = createSession();
  session.assistantPlaying = true;
  session.responseActive = true;

  session.userSpeechMs = 240;

  assert.deepEqual(posted, []);
  assert.deepEqual(sent, []);
  assert.equal(session.assistantPlaying, true);
  assert.equal(session.responseActive, true);
});

test('native MiniCPM rejects text locally without sending an unsupported event', async () => {
  const { session, posted, sent, diagnostics } = createSession();

  const accepted = await session.sendTextTurn('识别这个瓶子');

  assert.equal(accepted, false);
  assert.deepEqual(posted, []);
  assert.deepEqual(sent, []);
  assert.equal(diagnostics.at(-1).event, 'native_text_input_router_only');
});

test('a completed tool result waits without interrupting an active response', () => {
  const { session, posted, sent, dispatchedToolResults, readyToolResults } = createSession();
  session.assistantPlaying = true;
  session.responseActive = true;
  session.enqueueToolResult({
    jobId: 'search-1',
    question: '介绍一下这家公司',
    result: 'Luckin Coffee summary',
  });

  session.dispatchQueuedTextInput();
  assert.equal(session.pendingToolResults.length, 1);
  assert.deepEqual(readyToolResults, []);
  assert.deepEqual(posted, []);
  assert.deepEqual(sent, []);

  session.responseActive = false;
  session.assistantPlaying = false;
  session.lastInteractiveInputAt = Date.now() - 3_000;
  session.dispatchQueuedTextInput();

  assert.equal(session.pendingToolResults.length, 0);
  assert.deepEqual(sent, []);
  assert.deepEqual(readyToolResults, [{
    jobId: 'search-1',
    question: '介绍一下这家公司',
    result: 'Luckin Coffee summary',
  }]);
  assert.deepEqual(dispatchedToolResults, ['search-1']);
});

test('a tool result is delivered directly without native text append', () => {
  const { session, sent, readyToolResults, diagnostics } = createSession();
  session.enqueueToolResult({
    jobId: 'search-correlated',
    question: '这个品牌是什么',
    result: 'Luckin Coffee is a Chinese coffee chain.',
  });
  session.lastInteractiveInputAt = Date.now() - 3_000;
  session.dispatchQueuedTextInput();

  assert.deepEqual(sent, []);
  assert.equal(readyToolResults.at(-1).jobId, 'search-correlated');
  assert.match(readyToolResults.at(-1).result, /Luckin Coffee/);
  assert.deepEqual(
    diagnostics.filter((event) => event.event.startsWith('search_result_')).map((event) => event.event),
    ['search_result_queued', 'search_result_dispatched'],
  );
});

test('Realtime input transcription events drive user text callbacks', () => {
  const { session, userTexts } = createSession();

  session.handleEvent({ type: 'conversation.item.input_audio_transcription.delta', delta: '农夫' });
  session.handleEvent({
    type: 'conversation.item.input_audio_transcription.completed',
    transcript: '农夫山泉',
  });

  assert.deepEqual(userTexts, [
    { text: '农夫', final: false },
    { text: '农夫山泉', final: true },
  ]);
});

test('Qwen defers the first image until a previous audio append exists', () => {
  const frame = 'dGVzdC1qcGVn';
  const { session, sent } = createSession(frame, 'qwen_omni');

  session.sendAudio(new Int16Array([1, -1, 2, -2]), true);

  assert.equal(sent.length, 1);
  assert.equal(sent[0].type, 'input_audio_buffer.append');
  assert.equal(typeof sent[0].audio, 'string');
  assert.equal('video_frames' in sent[0], false);
});

test('Qwen sends later audio before the deferred JPEG frame', () => {
  const frame = 'dGVzdC1qcGVn';
  const { session, sent } = createSession(frame, 'qwen_omni');

  session.sendAudio(new Int16Array([1, -1]), true);
  session.sendAudio(new Int16Array([2, -2]), true);

  assert.equal(sent.length, 3);
  assert.equal(sent[0].type, 'input_audio_buffer.append');
  assert.equal(sent[1].type, 'input_audio_buffer.append');
  assert.deepEqual(sent[2], { type: 'input_image_buffer.append', image: frame });
});

test('Qwen user speech cancels the active response and clears queued playback once', () => {
  const { session, posted, sent, states, assistantTexts, diagnostics } = createSession(null, 'qwen_omni');
  session.responseId = 'qwen-speaking';
  session.responseActive = true;
  session.assistantPlaying = true;
  session.assistantTranscript = '这是一段被用户打断的回答';

  assert.equal(session.interruptQwenResponse('voice-1', 240, 900, 350), true);
  assert.equal(session.interruptQwenResponse('voice-1', 260, 900, 350), false);

  assert.deepEqual(sent, [{ type: 'response.cancel' }]);
  assert.deepEqual(posted, [{ type: 'clear', cancelResponse: false }]);
  assert.deepEqual(assistantTexts.at(-1), {
    text: '这是一段被用户打断的回答',
    final: true,
    toolJobId: undefined,
    turnId: undefined,
  });
  assert.equal(states.at(-1), 'listening');
  assert.equal(diagnostics.at(-1).event, 'qwen_response_interrupted_by_user');
  assert.equal(session.responseActive, false);
  assert.equal(session.assistantPlaying, false);
});

test('Qwen clears completed buffered playback without cancelling a finished response', () => {
  const { session, posted, sent, diagnostics } = createSession(null, 'qwen_omni');
  session.responseActive = false;
  session.assistantPlaying = true;

  assert.equal(session.interruptQwenResponse('voice-2', 240, 900, 350), true);

  assert.deepEqual(sent, []);
  assert.deepEqual(posted, [{ type: 'clear', cancelResponse: false }]);
  assert.equal(diagnostics.at(-1).cancel_event_sent, false);
});

test('playback acknowledgement is sent only for the MiniCPM dialect', () => {
  const { session: qwenSession, sent: qwenSent } = createSession(null, 'qwen_omni');
  const { session: minicpmSession, sent: minicpmSent } = createSession();

  qwenSession.acknowledgePlayback('qwen-response', 640);
  minicpmSession.acknowledgePlayback('minicpm-response', 640);

  assert.deepEqual(qwenSent, []);
  assert.deepEqual(minicpmSent, [{
    type: 'playback.ack',
    response_id: 'minicpm-response',
    item_id: 'item_minicpm-response',
    played_ms: 640,
    committed_ms: 640,
  }]);
});

test('Qwen text and transcription events use their native payload fields', () => {
  const { session, assistantTexts, userTexts, diagnostics } = createSession(null, 'qwen_omni');

  session.handleEvent({
    type: 'conversation.item.input_audio_transcription.delta',
    text: '香',
    stash: '港',
  });
  session.handleEvent({ type: 'response.created', response: { id: 'qwen-response' } });
  session.handleEvent({ type: 'response.text.delta', response_id: 'qwen-response', delta: 'API_' });
  session.handleEvent({ type: 'response.text.done', response_id: 'qwen-response', text: 'API_OK' });
  session.handleEvent({
    type: 'conversation.item.input_audio_transcription.completed',
    transcript: '香港天气',
  });

  assert.deepEqual(userTexts, [
    { text: '香港', final: false },
    { text: '香港天气', final: true },
  ]);
  const nativeAsr = diagnostics.find((event) => event.event === 'qwen_native_asr_completed');
  assert.equal(nativeAsr.transcript, '香港天气');
  assert.equal(nativeAsr.has_transcript, true);
  assert.deepEqual(assistantTexts.at(-1), {
    text: 'API_OK', final: true, toolJobId: undefined, turnId: undefined,
  });
});

test('Qwen does not generate WAV audio or invoke the local ASR callback', () => {
  const { session, userTurnAudios, diagnostics } = createSession(null, 'qwen_omni');
  session.turnHasUserActivity = true;
  session.turnAudio = [new Int16Array(16_000)];
  session.turnSamples = 16_000;

  session.dispatchUserTurn('voice-qwen');

  assert.deepEqual(userTurnAudios, []);
  assert.equal(session.turnSamples, 0);
  assert.deepEqual(session.turnAudio, []);
  assert.equal(diagnostics.at(-1).event, 'qwen_local_asr_skipped');
});

test('Qwen function calls are emitted once with parsed arguments', () => {
  const { session, functionCalls, diagnostics } = createSession(null, 'qwen_omni');
  const event = {
    type: 'response.function_call_arguments.done',
    name: 'jiuwen_research',
    call_id: 'call-weather',
    arguments: '{"query":"香港今天的天气"}',
  };

  session.handleEvent(event);
  session.handleEvent(event);

  assert.deepEqual(functionCalls, [{
    name: 'jiuwen_research',
    callId: 'call-weather',
    arguments: '{"query":"香港今天的天气"}',
    query: '香港今天的天气',
  }]);
  assert.equal(diagnostics.at(-1).event, 'qwen_tool_call_received');
});

test('Qwen tool results wait while busy then return through the native call id', () => {
  const {
    session, sent, dispatchedToolResults, readyToolResults, assistantTexts,
  } = createSession(null, 'qwen_omni');
  session.responseActive = true;
  assert.equal(session.enqueueToolResult({
    jobId: 'search-weather',
    question: '香港今天的天气',
    result: '香港今天有雨。',
    callId: 'call-weather',
  }), true);

  session.dispatchQueuedTextInput();
  assert.equal(session.pendingToolResults.length, 1);
  assert.deepEqual(sent, []);

  session.responseActive = false;
  session.dispatchQueuedTextInput();

  assert.equal(session.pendingToolResults.length, 0);
  assert.deepEqual(dispatchedToolResults, ['search-weather']);
  assert.deepEqual(readyToolResults, []);
  assert.deepEqual(sent, [
    {
      type: 'conversation.item.create',
      item: {
        type: 'function_call_output',
        call_id: 'call-weather',
        output: '香港今天有雨。',
      },
    },
    {
      type: 'conversation.item.create',
      item: {
        type: 'message',
        role: 'user',
        content: [{
          type: 'input_text',
          text: [
            '[Jiuwen tool result is ready]',
            'The completed tool call belongs to this earlier user request: 香港今天的天气',
            'Answer that request now using the function result immediately above.',
            'Do not answer a newer conversation turn and do not repeat this tool call.',
          ].join('\n'),
        }],
      },
    },
    { type: 'response.create' },
  ]);

  session.handleEvent({ type: 'response.created', response: { id: 'answer-weather' } });
  session.handleEvent({
    type: 'response.text.done',
    response_id: 'answer-weather',
    text: '香港今天有雨，出门请带伞。',
  });
  assert.deepEqual(assistantTexts.at(-1), {
    text: '香港今天有雨，出门请带伞。',
    final: true,
    toolJobId: 'search-weather',
    turnId: undefined,
  });
});

test('Qwen text input uses a native conversation item and response request', async () => {
  const { session, sent } = createSession(null, 'qwen_omni');

  assert.equal(await session.sendTextTurn('查询香港天气'), true);
  assert.deepEqual(sent, [
    {
      type: 'conversation.item.create',
      item: {
        type: 'message',
        role: 'user',
        content: [{ type: 'input_text', text: '查询香港天气' }],
      },
    },
    { type: 'response.create' },
  ]);
});

test('Qwen session update includes Gateway-provided tools', async () => {
  const tools = [{ type: 'function', function: { name: 'jiuwen_research' } }];
  let socket;
  class StartupSocket {
    static OPEN = 1;

    constructor() {
      socket = this;
      this.readyState = StartupSocket.OPEN;
      this.sent = [];
    }

    close() {}
    send(message) { this.sent.push(JSON.parse(message)); }
  }
  globalThis.window = globalThis;
  globalThis.WebSocket = StartupSocket;
  const session = new RealtimeDuplexSession(
    {
      url: 'ws://example.test/realtime',
      dialect: 'qwen_omni',
      tools,
    },
    {
      getVideoFrame: () => null,
      onAssistantText: () => undefined,
      onUserText: () => undefined,
      onUserTurnStarted: () => undefined,
      onUserTurnAudio: () => undefined,
      onState: () => undefined,
      onError: () => undefined,
    },
  );

  const opening = session.openSocket();
  socket.onopen();
  await opening;

  assert.deepEqual(socket.sent[0].session.tools, tools);
  assert.match(socket.sent[0].session.instructions, /MUST call jiuwen_research in the same turn/);
  assert.match(socket.sent[0].session.instructions, /today's weather MUST produce a jiuwen_research function call/);
  assert.doesNotMatch(socket.sent[0].session.instructions, /我目前不知道，需要搜索确认/);
});

test('assistant answers are no longer coupled to local ASR turn ids', () => {
  const { session, assistantTexts } = createSession();
  session.turnHasUserActivity = true;
  session.turnAudio = [new Int16Array(16_000)];
  session.turnSamples = 16_000;
  session.dispatchUserTurn('voice-turn-1');
  session.handleEvent({ type: 'response.output.delta', kind: 'text', text: '瓶身品牌是农夫山泉。' });
  session.handleEvent({ type: 'response.listen' });

  assert.deepEqual(assistantTexts.at(-1), {
    text: '瓶身品牌是农夫山泉。', final: true, toolJobId: undefined, turnId: undefined,
  });
});

test('a completed tool answer is not attributed to the next model response', () => {
  const { session, assistantTexts, readyToolResults } = createSession();
  session.enqueueToolResult({ jobId: 'search-old', question: '旧问题', result: '旧结果' });
  session.lastInteractiveInputAt = Date.now() - 3_000;
  session.dispatchQueuedTextInput();
  session.handleEvent({ type: 'response.output.delta', kind: 'text', text: '新的普通回答' });
  session.handleEvent({ type: 'response.listen' });

  assert.equal(readyToolResults.at(-1).jobId, 'search-old');
  assert.equal(assistantTexts.at(-1).toolJobId, undefined);
});

test('current task monitoring is disabled without removing its implementation', () => {
  const { session, sent } = createSession();

  session.updateContext('continuously translate new English text', []);
  session.lastTaskReminderAt = Date.now() - 6_000;
  session.dispatchQueuedTextInput();

  assert.equal(session.activeTask, '');
  assert.equal(session.hasActiveTask, false);
  assert.equal(session.pendingTaskControl, null);
  assert.equal(sent.some((event) => /当前任务|translate/.test(String(event.text || ''))), false);
  assert.doesNotMatch(session.contextInstructions, /continuously translate new English text/);
});

test('decoded response chunks stay ordered before the playback drain', async () => {
  const { session, posted } = createSession();
  const decoded = [];
  session.decodeOutputAudio = async (_event, encoded) => {
    if (encoded === 'first') await new Promise((resolve) => setTimeout(resolve, 5));
    decoded.push(encoded);
    return new Int16Array(encoded === 'first' ? [1] : [2]);
  };

  session.handleEvent({ type: 'response.created', response: { id: 'response-ordered' } });
  session.handleEvent({
    type: 'response.audio.delta',
    response_id: 'response-ordered',
    delta: 'first',
  });
  session.handleEvent({
    type: 'response.audio.delta',
    response_id: 'response-ordered',
    delta: 'second',
  });
  session.handleEvent({ type: 'response.done', response_id: 'response-ordered' });
  await new Promise((resolve) => setTimeout(resolve, 15));

  assert.deepEqual(decoded, ['first', 'second']);
  assert.deepEqual(posted.map((message) => message.type), ['audio', 'audio', 'drain']);
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
    { url: 'ws://example.test/realtime', refAudio: '' },
    {
      getVideoFrame: () => null,
      onAssistantText: () => undefined,
      onUserTurnStarted: () => undefined,
      onUserTurnAudio: () => undefined,
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
