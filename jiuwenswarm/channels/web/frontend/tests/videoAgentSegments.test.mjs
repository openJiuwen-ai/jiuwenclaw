import assert from 'node:assert/strict';
import test from 'node:test';

import {
  advanceMeaningfulVideoAgentVersion,
  collectVideoAgentTurns,
  evaluateVoiceTranscriptRoute,
} from '../node_modules/.cache/video-agent-segments/videoAgentSegments.js';

test('routes local ASR when MiniCPM does not emit a native transcript', () => {
  const decision = evaluateVoiceTranscriptRoute(null, '今天香港天气怎么样？', 'local', 1_000);

  assert.equal(decision.route, true);
  assert.equal(decision.stamp.key, '今天香港天气怎么样');
});

test('deduplicates native and local transcripts for one voice turn', () => {
  const native = evaluateVoiceTranscriptRoute(null, '今天香港天气怎么样？', 'native', 1_000);
  const local = evaluateVoiceTranscriptRoute(
    native.stamp,
    '今天香港天气怎么样。',
    'local',
    2_000,
  );

  assert.equal(native.route, true);
  assert.equal(local.route, false);
});

test('does not suppress a deliberate repeated request from the same ASR source', () => {
  const first = evaluateVoiceTranscriptRoute(null, '搜索香港天气', 'local', 1_000);
  const second = evaluateVoiceTranscriptRoute(first.stamp, '搜索香港天气', 'local', 2_000);

  assert.equal(first.route, true);
  assert.equal(second.route, true);
});

test('empty ASR activity does not supersede the latest meaningful user turn', () => {
  let latestMeaningfulVersion = advanceMeaningfulVideoAgentVersion(0, 1, '当我喝水时提醒我小心');
  latestMeaningfulVersion = advanceMeaningfulVideoAgentVersion(latestMeaningfulVersion, 2, '');
  latestMeaningfulVersion = advanceMeaningfulVideoAgentVersion(latestMeaningfulVersion, 3, '。');

  assert.equal(latestMeaningfulVersion, 1);
});

test('a later meaningful user turn supersedes an older task result', () => {
  let latestMeaningfulVersion = advanceMeaningfulVideoAgentVersion(0, 1, '看到我喝水时提醒我');
  latestMeaningfulVersion = advanceMeaningfulVideoAgentVersion(
    latestMeaningfulVersion,
    4,
    '停止当前任务',
  );

  assert.equal(latestMeaningfulVersion, 4);
});

test('keeps separate search requests from consecutive user turns', () => {
  const turns = collectVideoAgentTurns([
    {
      order: 1,
      text: '搜索农夫山泉的相关信息',
      realtimeAnswer: '我帮你搜索农夫山泉。',
      requestVersion: 1,
    },
    {
      order: 2,
      text: '今天香港天气怎么样',
      realtimeAnswer: '我再帮你查询香港天气。',
      requestVersion: 2,
    },
  ]);

  assert.deepEqual(turns, [
    {
      version: 1,
      question: '搜索农夫山泉的相关信息',
      realtimeAnswer: '我帮你搜索农夫山泉。',
    },
    {
      version: 2,
      question: '今天香港天气怎么样',
      realtimeAnswer: '我再帮你查询香港天气。',
    },
  ]);
});

test('combines duplicate ASR fragments only within the same turn', () => {
  const turns = collectVideoAgentTurns([
    { order: 2, text: '相关信息', realtimeAnswer: '正在搜索', requestVersion: 3 },
    { order: 1, text: '搜索农夫山泉', realtimeAnswer: '', requestVersion: 3 },
    { order: 3, text: '相关信息', realtimeAnswer: '', requestVersion: 3 },
  ]);

  assert.deepEqual(turns, [{
    version: 3,
    question: '搜索农夫山泉。相关信息',
    realtimeAnswer: '正在搜索',
  }]);
});
