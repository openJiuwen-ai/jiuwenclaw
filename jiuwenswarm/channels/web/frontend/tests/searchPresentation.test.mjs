import assert from 'node:assert/strict';
import test from 'node:test';
import {
  assistantSpeechText,
  groundedSearchAnswer,
} from '../node_modules/.cache/search-presentation/searchPresentation.js';

test('groundedSearchAnswer keeps the answer but removes evidence metadata and citations', () => {
  const result = [
    '九问检索摘要（主对话模型根据网页正文生成）',
    '检索时间：2026-08-18T15:19:25+08:00',
    '问题：今天香港天气怎么样？',
    '搜索词：香港今日天气',
    '',
    '今天香港大致多云，有几阵骤雨，外出最好带伞。[来源1][来源2]',
    '',
    '来源：',
    '[来源1] 香港天文台 - https://example.com/weather',
  ].join('\n');

  assert.equal(
    groundedSearchAnswer(result),
    '今天香港大致多云，有几阵骤雨，外出最好带伞。',
  );
});

test('assistantSpeechText removes markdown, citations, URLs, and caps long speech', () => {
  assert.equal(
    assistantSpeechText('**香港天气**大致多云。[来源1] https://example.com'),
    '香港天气大致多云。',
  );
  const spoken = assistantSpeechText(`第一句很有用。${'补充内容'.repeat(80)}`, 40);
  assert.ok(spoken.length <= 41);
  assert.match(spoken, /。$/);
});
