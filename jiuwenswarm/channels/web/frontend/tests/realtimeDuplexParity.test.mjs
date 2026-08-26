import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { RealtimeDuplexSession } from '../node_modules/.cache/realtime-duplex-parity/realtimeDuplex.js';

test('native duplex uploads audio after websocket open and supports voice-only input', async () => {
  const sent = [];
  const intervals = [];
  const worklets = [];
  const socketUrls = [];

  class FakeAudioContext {
    constructor(options = {}) {
      this.sampleRate = options.sampleRate || 16_000;
      this.audioWorklet = { addModule: async () => undefined };
      this.destination = {};
    }
    createMediaStreamSource() { return { connect: (target) => target }; }
    createGain() { return { gain: { value: 1 }, connect: () => undefined }; }
    async resume() {}
    async close() {}
  }

  class FakeAudioWorkletNode {
    constructor() {
      this.port = { onmessage: null, postMessage: () => undefined };
      worklets.push(this);
    }
    connect(target) { return target; }
  }

  class FakeWebSocket {
    static OPEN = 1;
    constructor(url) {
      socketUrls.push(url);
      this.readyState = 0;
      queueMicrotask(() => {
        this.readyState = FakeWebSocket.OPEN;
        this.onopen?.();
      });
    }
    send(payload) { sent.push(JSON.parse(payload)); }
    close() { this.readyState = 3; }
  }

  const original = {
    AudioContext: globalThis.AudioContext,
    AudioWorkletNode: globalThis.AudioWorkletNode,
    WebSocket: globalThis.WebSocket,
    navigator: Object.getOwnPropertyDescriptor(globalThis, 'navigator'),
    window: globalThis.window,
  };
  globalThis.AudioContext = FakeAudioContext;
  globalThis.AudioWorkletNode = FakeAudioWorkletNode;
  globalThis.WebSocket = FakeWebSocket;
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      mediaDevices: {
        getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }),
      },
    },
  });
  globalThis.window = {
    setInterval(callback) { intervals.push(callback); return intervals.length; },
    clearInterval() {},
    setTimeout: globalThis.setTimeout,
    clearTimeout: globalThis.clearTimeout,
  };

  const callbacks = (videoFrame) => ({
    getVideoFrame: () => videoFrame,
    onAssistantText() {},
    onUserText() {},
    onUserTurnStarted() {},
    onUserTurnAudio() {},
    onState() {},
    onError() {},
  });

  try {
    const session = new RealtimeDuplexSession({
      url: 'ws://127.0.0.1:17862/v1/realtime',
      model: 'openbmb/MiniCPM-o-4_5',
      refAudio: 'data:audio/wav;base64,',
    }, callbacks('jpeg'));

    await session.start();
    const nativeUrl = new URL(socketUrls[0]);
    assert.equal(nativeUrl.searchParams.get('duplex'), '1');
    assert.equal(nativeUrl.searchParams.has('model'), false);
    assert.equal(nativeUrl.searchParams.has('minicpmo45_native_duplex'), false);
    assert.equal(nativeUrl.searchParams.has('autostart'), false);
    const { instructions, ...sessionConfig } = sent[0].session;
    assert.deepEqual({ ...sent[0], session: sessionConfig }, {
      type: 'session.update',
      session: {
        modalities: ['audio', 'text'],
        voice: 'default',
        ref_audio: 'data:audio/wav;base64,',
        extra_body: { auto_response: true, minicpmo45_native_duplex: true },
      },
    });
    assert.match(instructions, /^Streaming Omni Conversation\.\n/);
    assert.match(instructions, /我目前不知道，需要搜索确认/);
    assert.match(instructions, /香港今天天气/);

    worklets[1].port.onmessage({ data: new Int16Array(3_200).buffer });
    intervals[0]();
    assert.equal(sent[1]?.type, 'input_audio_buffer.append');
    assert.deepEqual(sent[1]?.video_frames, ['jpeg']);
    assert.equal(sent[1]?.force_listen, undefined);
    session.stop();

    const voiceSession = new RealtimeDuplexSession({
      url: 'ws://127.0.0.1:17862/v1/realtime',
      model: 'openbmb/MiniCPM-o-4_5',
      refAudio: 'data:audio/wav;base64,',
    }, callbacks(null));
    await voiceSession.start();
    worklets[3].port.onmessage({ data: new Int16Array(3_200).buffer });
    intervals[1]();
    const voiceAppend = sent.findLast((event) => event.type === 'input_audio_buffer.append');
    assert.equal('video_frames' in voiceAppend, false);
    assert.equal('force_listen' in voiceAppend, false);
    voiceSession.stop();
  } finally {
    globalThis.AudioContext = original.AudioContext;
    globalThis.AudioWorkletNode = original.AudioWorkletNode;
    globalThis.WebSocket = original.WebSocket;
    if (original.navigator) Object.defineProperty(globalThis, 'navigator', original.navigator);
    else delete globalThis.navigator;
    if (original.window === undefined) delete globalThis.window;
    else globalThis.window = original.window;
  }
});

test('MiniCPM buffering follows target while hard barge-in remains JoyAI-only', async () => {
  const realtime = await readFile(new URL('../src/utils/realtimeDuplex.ts', import.meta.url), 'utf8');
  const playback = await readFile(new URL('../public/duplex-playback.js', import.meta.url), 'utf8');
  const joyai = await readFile(new URL('../src/components/VideoLivePanel/joyaiProvider.ts', import.meta.url), 'utf8');

  assert.match(realtime, /INITIAL_PLAYBACK_BUFFER_MS = 400/);
  assert.doesNotMatch(realtime, /force_listen/);
  assert.doesNotMatch(realtime, /interruptForUserInput|bargeInCandidate/);
  assert.match(playback, /initialBufferSamples = Math\.round\(sampleRate \* 0\.4\)/);
  assert.match(joyai, /onSpeechStart[^]+interruptTts\(\)/);
});
