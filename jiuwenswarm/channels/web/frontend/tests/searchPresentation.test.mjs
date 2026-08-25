import assert from 'node:assert/strict';
import test from 'node:test';
import {
  assistantSpeechText,
  groundedSearchAnswer,
  joyaiSearchAnswerInstruction,
  joyaiSearchFinalAnswer,
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

test('joyaiSearchAnswerInstruction asks JoyAI to answer from evidence without exposing process', () => {
  const instruction = joyaiSearchAnswerInstruction(
    '今天香港天气怎么样？',
    '搜索接口受限，我改用天气 API。香港今天多云有骤雨，28 至 33 度。',
  );

  assert.match(instruction, /【原问题】\n今天香港天气怎么样？/);
  assert.match(instruction, /Core Agent 已验证资料/);
  assert.match(instruction, /香港今天多云有骤雨/);
  assert.match(instruction, /禁止在回答中复述/);
  assert.match(instruction, /<\/response> 最终回答/);
  assert.match(instruction, /不得输出 `<\/silence>` 或 `<\/delegation>`/);
  assert.ok(instruction.length <= 1_950);
});

test('joyaiSearchAnswerInstruction preserves both ends of long evidence', () => {
  const result = `开头事实${'过程'.repeat(2_000)}结尾结论与来源`;
  const instruction = joyaiSearchAnswerInstruction('问题', result);

  assert.match(instruction, /开头事实/);
  assert.match(instruction, /结尾结论与来源/);
  assert.match(instruction, /中间内容因长度限制省略/);
  assert.ok(instruction.length <= 1_950);
});

test('joyaiSearchFinalAnswer accepts only an explicit JoyAI response action', () => {
  assert.equal(
    joyaiSearchFinalAnswer({ decision: 'response', response: '<think>过程</think>最终答案' }),
    '最终答案',
  );
  assert.equal(
    joyaiSearchFinalAnswer({ decision: 'delegation', response: '我继续搜索' }),
    '',
  );
  assert.equal(joyaiSearchFinalAnswer({ decision: 'silence', response: '' }), '');
});
