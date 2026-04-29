import { describe, expect, it } from 'vitest';

import { stripInlineToolProtocol, stripResidualInlineToolProtocol } from './toolProtocol';

describe('stripInlineToolProtocol', () => {
  it('preserves plain text after outer tag blob without function<tool_sep>', () => {
    const text = '前文<tool_calls_begin>随机内容</tool_calls_end>后续';
    expect(stripInlineToolProtocol(text)).toBe('前文后续');
  });

  it('strips whitelist todo protocol and leaves surrounding text', () => {
    const text =
      '好的。<tool_calls_begin><tool_call_begin>function<tool_sep>todo_create{"tasks": ["a"]}</tool_call_end>完成';
    expect(stripInlineToolProtocol(text)).toBe('好的。完成');
  });

  it('does not strip non-whitelist tool names', () => {
    const text = 'function<tool_sep>run_command{"cmd": "ls"}';
    expect(stripInlineToolProtocol(text)).toBe(text);
  });
});

describe('stripResidualInlineToolProtocol', () => {
  it('no-ops when no explicit tool tags', () => {
    const text = 'function<tool_sep>todo_insert{}';
    expect(stripResidualInlineToolProtocol(text)).toBe(text);
  });

  it('strips when explicit tags present', () => {
    const text = '<tool_calls_begin>x</tool_calls_end>';
    expect(stripResidualInlineToolProtocol(text)).toBe('');
  });
});
