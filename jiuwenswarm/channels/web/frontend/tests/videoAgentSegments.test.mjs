import assert from 'node:assert/strict';
import test from 'node:test';

import {
  advanceMeaningfulVideoAgentVersion,
  collectVideoAgentTurns,
} from '../node_modules/.cache/video-agent-segments/videoAgentSegments.js';

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
