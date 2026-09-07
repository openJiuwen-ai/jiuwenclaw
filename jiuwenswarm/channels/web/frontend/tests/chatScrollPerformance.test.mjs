import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const indexSource = readFileSync(
  new URL('../src/components/ChatPanel/index.tsx', import.meta.url),
  'utf8',
);
const styles = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');

test('coalesces chat scroll bookkeeping to one animation frame', () => {
  assert.match(indexSource, /scrollFrameRef\.current !== null/);
  assert.match(indexSource, /window\.requestAnimationFrame\(\(\) => \{/);
  assert.match(indexSource, /window\.cancelAnimationFrame\(scrollFrameRef\.current\)/);
});

test('upward wheel intent pauses automatic bottom following immediately', () => {
  assert.match(
    indexSource,
    /if \(e\.deltaY < 0\) \{[\s\S]*?stickToBottomUntilStableRef\.current = false;[\s\S]*?userScrolledUpRef\.current = true;/,
  );
});

test('skips rendering offscreen timeline blocks when the webview supports it', () => {
  assert.match(styles, /@supports \(content-visibility: auto\)/);
  assert.match(styles, /\.chat-timeline > \*:not\(\.timeline-collapse\)/);
  assert.match(styles, /contain-intrinsic-size: auto 120px/);
});
