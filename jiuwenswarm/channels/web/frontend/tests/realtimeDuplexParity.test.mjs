import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

import { RealtimeDuplexSession } from '../node_modules/.cache/realtime-duplex-parity/realtimeDuplex.js';

test('native duplex uploads microphone audio after websocket open without waiting for session.created', async () => {
  const sent = [];
  const intervals = [];
  const worklets = [];

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
    constructor() {
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
  };

  try {
    const session = new RealtimeDuplexSession({
      url: 'ws://127.0.0.1:17862/v1/realtime?duplex=1',
      model: 'openbmb/MiniCPM-o-4_5',
      refAudio: 'data:audio/wav;base64,',
    }, {
      getVideoFrame: () => 'jpeg',
      onAssistantText() {},
      onUserText() {},
      onUserTurnStarted() {},
      onUserTurnAudio() {},
      onState() {},
      onError() {},
    });

    await session.start();
    assert.deepEqual(sent[0], {
      type: 'session.update',
      session: {
        modalities: ['audio', 'text'],
        voice: 'default',
        ref_audio: 'data:audio/wav;base64,',
        instructions: 'Streaming Omni Conversation.',
        extra_body: { auto_response: true, minicpmo45_native_duplex: true },
      },
    });
    assert.equal(intervals.length, 1);

    worklets[1].port.onmessage({ data: new Int16Array(3_200).buffer });
    intervals[0]();

    assert.equal(sent[1]?.type, 'input_audio_buffer.append');
    assert.deepEqual(sent[1]?.video_frames, ['jpeg']);

    worklets[1].port.onmessage({ data: new Int16Array(3_184).buffer });
    const appendsBeforeStop = sent.filter((event) => event.type === 'input_audio_buffer.append').length;
    session.stop();
    assert.equal(sent.at(-1)?.type, 'session.close');
    assert.equal(sent.filter((event) => event.type === 'input_audio_buffer.append').length, appendsBeforeStop);

    const voiceSession = new RealtimeDuplexSession({
      url: 'ws://127.0.0.1:17862/v1/realtime?duplex=1',
      model: 'openbmb/MiniCPM-o-4_5',
      refAudio: 'data:audio/wav;base64,',
    }, {
      getVideoFrame: () => null,
      onAssistantText() {},
      onUserText() {},
      onUserTurnStarted() {},
      onUserTurnAudio() {},
      onState() {},
      onError() {},
    });
    await voiceSession.start();
    worklets[3].port.onmessage({ data: new Int16Array(3_200).buffer });
    intervals[1]();
    const voiceAppend = sent.findLast((event) => event.type === 'input_audio_buffer.append');
    assert.equal('video_frames' in voiceAppend, false);
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

test('camera and playback constants match the official single-camera client', async () => {
  const realtime = await readFile(new URL('../src/utils/realtimeDuplex.ts', import.meta.url), 'utf8');
  const playback = await readFile(new URL('../public/duplex-playback.js', import.meta.url), 'utf8');
  const panel = await readFile(new URL('../src/components/VideoLivePanel/index.tsx', import.meta.url), 'utf8');

  assert.match(realtime, /INITIAL_PLAYBACK_BUFFER_MS = 400/);
  assert.doesNotMatch(realtime, /REBUFFER_PLAYBACK_BUFFER_MS|rebufferMs/);
  assert.match(playback, /initialBufferSamples = Math\.round\(sampleRate \* 0\.4\)/);
  assert.doesNotMatch(playback, /terminalPadding|sampleRate \* 0\.12/);
  assert.match(panel, /getUserMedia\(\{\s*video: true,\s*audio: false,?\s*\}\)/);
  assert.match(realtime, /microphoneUploadEnabled = false;\s*if \(this\.sendTimer[^]+this\.send\(\{ type: 'session\.close' \}\)/);
});
