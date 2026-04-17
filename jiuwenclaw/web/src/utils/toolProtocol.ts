/**
 * 前端内联工具协议清洗工具（与后端 stream_content_sanitize.py 同语义）。
 *
 * 剥离模型/网关写入 delta.content 后泄漏到 chat.final/chat.delta 中的协议串：
 *   <tool_calls_begin><tool_call_begin>function<tool_sep>tool_name{...}</tool_calls_end>
 *
 * 仅影响展示与 TTS，不修改 chatStore 持久化内容。
 */

// ---------------------------------------------------------------------------
// 常量
// ---------------------------------------------------------------------------

const OPEN_TAGS = ['<tool_calls_begin>', '<tool_call_begin>'] as const;
const CLOSE_TAGS = ['</tool_calls_end>', '</tool_call_end>'] as const;
const FUNC_SEP = 'function<tool_sep>';
const TOOL_NAME_RE = /^[A-Za-z0-9_]+/;

// 与后端保持一致的工具名白名单；STREAM_STRIP_INLINE_TOOLS 无法在浏览器端读取，默认白名单模式
const TOOL_WHITELIST: ReadonlySet<string> = new Set([
  'todo_create',
  'todo_complete',
  'todo_insert',
  'todo_remove',
  'todo_list',
]);

// ---------------------------------------------------------------------------
// 底层：平衡括号查找
// ---------------------------------------------------------------------------

function findBalancedJsonEnd(text: string, start: number): number {
  let depth = 0;
  let inString = false;
  let escapeNext = false;
  const n = text.length;

  for (let i = start; i < n; i++) {
    const ch = text[i];
    if (escapeNext) {
      escapeNext = false;
      continue;
    }
    if (inString) {
      if (ch === '\\') { escapeNext = true; }
      else if (ch === '"') { inString = false; }
      continue;
    }
    if (ch === '"') {
      inString = true;
    } else if (ch === '{' || ch === '[') {
      depth++;
    } else if (ch === '}' || ch === ']') {
      depth--;
      if (depth === 0) return i + 1;
    }
  }
  return -1; // 未闭合
}

// ---------------------------------------------------------------------------
// 底层：标签/锚点搜索
// ---------------------------------------------------------------------------

function findEarliestOpenTag(text: string, from = 0): { pos: number; tag: string } {
  let bestPos = -1;
  let bestTag = '';
  for (const tag of OPEN_TAGS) {
    const pos = text.indexOf(tag, from);
    if (pos !== -1 && (bestPos === -1 || pos < bestPos)) {
      bestPos = pos;
      bestTag = tag;
    }
  }
  return { pos: bestPos, tag: bestTag };
}

function findFuncSep(text: string, from = 0): number {
  return text.indexOf(FUNC_SEP, from);
}

function findCloseTags(text: string, from: number): number {
  /** 贪婪消费所有连续闭合标签（标签间允许空白），返回最后标签后的位置；无则 -1。 */
  let pos = from;
  let foundAny = false;
  while (true) {
    let bestIdx = -1;
    let bestTag = '';
    for (const tag of CLOSE_TAGS) {
      const idx = text.indexOf(tag, pos);
      if (idx !== -1 && (bestIdx === -1 || idx < bestIdx)) {
        bestIdx = idx;
        bestTag = tag;
      }
    }
    if (bestIdx === -1) break;
    const gap = text.slice(pos, bestIdx);
    if (gap.trim()) break; // 中间有非空白内容，停止
    pos = bestIdx + bestTag.length;
    foundAny = true;
  }
  return foundAny ? pos : -1;
}

function toolNameAllowed(name: string): boolean {
  return TOOL_WHITELIST.has(name);
}

function toolNameMayBeIncompletePrefix(name: string): boolean {
  return Array.from(TOOL_WHITELIST).some((tool) => tool.startsWith(name) && tool !== name);
}

function hasExplicitInlineToolTags(text: string): boolean {
  return OPEN_TAGS.some((tag) => text.includes(tag)) || CLOSE_TAGS.some((tag) => text.includes(tag));
}

// ---------------------------------------------------------------------------
// 核心：单次 pass 剥离
// ---------------------------------------------------------------------------

function stripOnePass(text: string): string {
  let pos = 0;
  const n = text.length;

  while (pos < n) {
    const { pos: tagPos } = findEarliestOpenTag(text, pos);
    const funcPos = findFuncSep(text, pos);

    if (tagPos === -1 && funcPos === -1) break;

    const useOuter = tagPos !== -1 && (funcPos === -1 || tagPos <= funcPos);

    if (useOuter) {
      const anchor = tagPos;
      const innerFunc = findFuncSep(text, anchor);

      if (innerFunc === -1) {
        const closeEnd = findCloseTags(text, anchor);
        if (closeEnd !== -1) {
          return text.slice(0, anchor) + text.slice(closeEnd);
        }
        return text.slice(0, anchor);
      }

      const nameStart = innerFunc + FUNC_SEP.length;
      const nameMatch = TOOL_NAME_RE.exec(text.slice(nameStart));
      if (!nameMatch) { pos = innerFunc + 1; continue; }
      const toolName = nameMatch[0];
      if (!toolNameAllowed(toolName)) {
        if (toolNameMayBeIncompletePrefix(toolName)) {
          return text.slice(0, anchor);
        }
        pos = innerFunc + 1;
        continue;
      }

      const jsonStart = nameStart + toolName.length;
      if (jsonStart >= n || text[jsonStart] !== '{') { pos = jsonStart; continue; }

      const jsonEnd = findBalancedJsonEnd(text, jsonStart);
      if (jsonEnd === -1) {
        // 截断：从锚点删至末尾
        return text.slice(0, anchor);
      }
      const closeEnd = findCloseTags(text, jsonEnd);
      const segEnd = closeEnd !== -1 ? closeEnd : jsonEnd;
      return text.slice(0, anchor) + text.slice(segEnd);
    } else {
      const anchor = funcPos;
      const nameStart = anchor + FUNC_SEP.length;
      const nameMatch = TOOL_NAME_RE.exec(text.slice(nameStart));
      if (!nameMatch) { pos = anchor + 1; continue; }
      const toolName = nameMatch[0];
      if (!toolNameAllowed(toolName)) {
        if (toolNameMayBeIncompletePrefix(toolName)) {
          return text.slice(0, anchor);
        }
        pos = anchor + 1;
        continue;
      }

      const jsonStart = nameStart + toolName.length;
      if (jsonStart >= n || text[jsonStart] !== '{') { pos = jsonStart; continue; }

      const jsonEnd = findBalancedJsonEnd(text, jsonStart);
      if (jsonEnd === -1) {
        return text.slice(0, anchor);
      }
      return text.slice(0, anchor) + text.slice(jsonEnd);
    }
  }
  return text;
}

// ---------------------------------------------------------------------------
// 公开 API
// ---------------------------------------------------------------------------

/**
 * 剥离文本中所有已闭合或截断的内联工具协议段。
 * 对完整文本（非流式）调用，适用于 chat.final 展示与 TTS。
 */
export function stripInlineToolProtocol(text: string): string {
  if (!text) return text;
  let result = text;
  let changed = true;
  while (changed) {
    const next = stripOnePass(result);
    changed = next !== result;
    result = next;
  }
  return result;
}

/**
 * 仅在文本里存在显式工具协议标签时再做兜底清洗，避免误删正常回答里的协议字面量示例。
 */
export function stripResidualInlineToolProtocol(text: string): string {
  if (!text || !hasExplicitInlineToolTags(text)) {
    return text;
  }
  return stripInlineToolProtocol(text);
}
