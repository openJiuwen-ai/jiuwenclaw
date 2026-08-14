import test from 'node:test';
import assert from 'node:assert/strict';

import {
  extractSvgBlocks,
  pickSvgBlockFor,
} from '../node_modules/.cache/history-record-content/historyRecordContent.mjs';

const SVG = '<svg xmlns="http://www.w3.org/2000/svg"><rect x="1" /></svg>';

test('extracts fenced svg blocks from a stored message', () => {
  const message = ['Here is a chart:', '```svg', SVG, '```', 'Hope that helps.'].join('\n');
  assert.deepEqual(extractSvgBlocks(message), [SVG]);
});

test('recovers an unterminated block, which is what truncation leaves behind', () => {
  // No closing fence: the server cut the message mid-diagram.
  const cut = ['Here is a chart:', '```svg', '<svg><g>'].join('\n');
  assert.deepEqual(extractSvgBlocks(cut), ['<svg><g>']);
});

test('ignores fences that are not svg diagrams', () => {
  const message = ['```xml', SVG, '```', '```js', 'const a = 1;', '```'].join('\n');
  assert.deepEqual(extractSvgBlocks(message), []);
});

test('handles tilde fences and language tags with trailing info', () => {
  assert.deepEqual(extractSvgBlocks(['~~~svg', SVG, '~~~'].join('\n')), [SVG]);
  assert.deepEqual(extractSvgBlocks(['```svg title="x"', SVG, '```'].join('\n')), [SVG]);
});

test('picks the diagram matching the partial content when a message has several', () => {
  const first = '<svg id="first"><rect x="1" /><rect x="2" /></svg>';
  const second = '<svg id="second"><circle r="3" /><circle r="4" /></svg>';
  const message = ['```svg', first, '```', 'and', '```svg', second, '```'].join('\n');

  // The rendered block is a prefix of the stored one — that is the probe.
  assert.equal(pickSvgBlockFor(message, '<svg id="second"><circle'), second);
  assert.equal(pickSvgBlockFor(message, '<svg id="first"><rect'), first);
});

test('returns the only block without needing a probe', () => {
  const message = ['```svg', SVG, '```'].join('\n');
  assert.equal(pickSvgBlockFor(message, '<svg xmlns'), SVG);
  assert.equal(pickSvgBlockFor(message, ''), SVG);
});

test('returns null rather than guessing when nothing matches', () => {
  const message = ['```svg', '<svg id="a"></svg>', '```', '```svg', '<svg id="b"></svg>', '```'].join('\n');
  assert.equal(pickSvgBlockFor(message, '<svg id="zzz"'), null);
  // No diagram in the record at all.
  assert.equal(pickSvgBlockFor('just prose', '<svg'), null);
});

test('recovers the full diagram from the partial the UI is holding', () => {
  // End-to-end shape of the export path: a long diagram, cut for display.
  const full = `<svg xmlns="http://www.w3.org/2000/svg">${'<rect x="1" />'.repeat(400)}</svg>`;
  const stored = ['Chart:', '```svg', full, '```'].join('\n');
  const partial = full.slice(0, 500);

  const recovered = pickSvgBlockFor(stored, partial);

  assert.equal(recovered, full);
  assert.ok(recovered.length > partial.length);
  assert.ok(recovered.endsWith('</svg>'));
});
