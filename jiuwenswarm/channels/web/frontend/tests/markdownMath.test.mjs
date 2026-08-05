import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MarkdownRenderer, repairCollapsedGfmTables } from '../node_modules/.cache/markdown-math/markdownRenderer.mjs';

function render(content) {
  return renderToStaticMarkup(createElement(MarkdownRenderer, { content }));
}

// KaTeX keeps the original TeX in a MathML annotation, which is the cheapest
// way to assert what actually got typeset.
function texOf(html) {
  const match = html.match(/annotation encoding="application\/x-tex">([^<]*)</);
  return match ? match[1] : null;
}

test('inline $...$ renders as inline KaTeX', () => {
  const html = render('Euler: $e^{i\\pi} + 1 = 0$');
  assert.match(html, /class="katex"/);
  assert.doesNotMatch(html, /katex-display/);
  assert.doesNotMatch(html, /katex-error/);
  assert.equal(texOf(html), 'e^{i\\pi} + 1 = 0');
});

test('a fenced $$ block renders as display KaTeX', () => {
  const html = render('$$\n\\int_0^\\infty e^{-x^2}\\,dx = \\frac{\\sqrt{\\pi}}{2}\n$$');
  assert.match(html, /katex-display/);
  assert.doesNotMatch(html, /katex-error/);
  assert.equal(texOf(html), '\\int_0^\\infty e^{-x^2}\\,dx = \\frac{\\sqrt{\\pi}}{2}');
});

// remark-math only opens a display block when $$ ends the line; anything after
// it on that line is fence meta, and meta containing $ disqualifies the fence.
// A one-line $$...$$ therefore typesets correctly but inline, not centered.
test('single-line $$...$$ typesets inline rather than as display', () => {
  const html = render('$$\\int_0^\\infty e^{-x^2}\\,dx = \\frac{\\sqrt{\\pi}}{2}$$');
  assert.match(html, /class="katex"/);
  assert.doesNotMatch(html, /katex-display/);
  assert.equal(texOf(html), '\\int_0^\\infty e^{-x^2}\\,dx = \\frac{\\sqrt{\\pi}}{2}');
});

test('invalid LaTeX degrades to an error span instead of throwing', () => {
  const html = render('Broken: $\\frac{$');
  assert.match(html, /katex-error/);
});

test('math and GFM tables coexist', () => {
  const html = render(['| a | b |', '| --- | --- |', '| $x^2$ | 2 |'].join('\n'));
  assert.match(html, /<table/);
  assert.match(html, /chat-markdown-table-wrap/);
  assert.match(html, /class="katex"/);
});

test('$ inside a fenced code block is left alone', () => {
  const html = render(['```bash', 'echo $VAR && awk \'{print $1}\'', '```'].join('\n'));
  assert.doesNotMatch(html, /katex/);
  assert.match(html, /\$VAR/);
  assert.match(html, /\$1/);
});

test('$ inside inline code is left alone', () => {
  const html = render('Use `$HOME` and `$PATH` here.');
  assert.doesNotMatch(html, /katex/);
  assert.match(html, /\$HOME/);
});

test('a partially streamed $$ fence typesets what has arrived so far', () => {
  const html = render('$$\n\\int_0^\\infty e^{-x^2}');
  assert.match(html, /katex-display/);
  assert.doesNotMatch(html, /katex-error/);
  assert.equal(texOf(html), '\\int_0^\\infty e^{-x^2}');
});

// Rough edge of the fence-meta rule above: until the closing $$ arrives, a
// one-line block is an opener whose body is still meta, so it renders empty.
test('known limitation: an unclosed single-line $$ renders an empty display box', () => {
  const html = render('$$\\frac{a}{b}');
  assert.match(html, /katex-display/);
  assert.equal(texOf(html), '');
});

// Accepted trade-off of singleDollarTextMath: currency pairs parse as math.
// Flip remarkMath to { singleDollarTextMath: false } if this ever outweighs
// inline math, which is far more common in model output.
test('known trade-off: a $5 / $10 pair is treated as inline math', () => {
  const html = render('It costs $5 and $10.');
  assert.match(html, /class="katex"/);
  assert.equal(texOf(html), '5 and ');
});

// repairCollapsedGfmTables runs before parsing, so pipe-heavy math has to survive
// it. It only rewrites a line that is entirely a table row and splits into three
// or more consistent columns with a delimiter row, which no math below satisfies.
test('repairCollapsedGfmTables leaves math with pipes untouched', () => {
  const untouched = [
    'Triangle: $$|x| - |y| \\le |x - y|$$',
    'Absolute value $|x| = x$ for positive x',
    'Set: $$\\{ x \\mid x > 0 \\}$$',
    'Norm: $$\\|v\\| - \\|w\\| \\le \\|v - w\\|$$',
    'Euler: $$e^{i\\pi} + 1 = 0$$',
  ];
  for (const input of untouched) {
    assert.equal(repairCollapsedGfmTables(input), input);
  }
});

test('repairCollapsedGfmTables still passes through a well-formed table', () => {
  const table = '| a | b |\n| --- | --- |\n| 1 | 2 |';
  assert.equal(repairCollapsedGfmTables(table), table);
});
