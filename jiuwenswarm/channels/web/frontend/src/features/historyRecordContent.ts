import { webRequest } from '../services/webClient';

export const HISTORY_RECORD_GET_METHOD = 'history.record.get';

interface HistoryRecordContentPayload {
  content?: unknown;
  truncated?: unknown;
}

export interface HistoryRecordContent {
  content: string;
  truncated: boolean;
}

export async function fetchHistoryRecordContent(
  sessionId: string,
  recordId: string,
): Promise<HistoryRecordContent | null> {
  if (!sessionId || !recordId) return null;
  try {
    const payload = await webRequest<HistoryRecordContentPayload>(HISTORY_RECORD_GET_METHOD, {
      session_id: sessionId,
      record_id: recordId,
      field: 'content',
    });
    if (typeof payload?.content !== 'string') return null;
    return { content: payload.content, truncated: payload.truncated === true };
  } catch {
    return null;
  }
}

const FENCE_OPEN = /^[ \t]{0,3}(`{3,}|~{3,})[ \t]*([A-Za-z0-9_-]*)/;
const FENCE_CLOSE = /^[ \t]{0,3}(`{3,}|~{3,})[ \t]*$/;

/**
 * Pull every fenced ```svg block out of a raw message. Scanned line by line
 * rather than by regex so an unterminated block — exactly what truncation
 * leaves behind — is still returned instead of silently skipped.
 */
export function extractSvgBlocks(content: string): string[] {
  const blocks: string[] = [];
  let buffer: string[] | null = null;
  let fenceChar = '';

  for (const line of content.split('\n')) {
    if (buffer === null) {
      const open = FENCE_OPEN.exec(line);
      if (open && open[2].toLowerCase() === 'svg') {
        buffer = [];
        fenceChar = open[1][0];
      }
      continue;
    }
    const close = FENCE_CLOSE.exec(line);
    if (close && close[1][0] === fenceChar) {
      blocks.push(buffer.join('\n'));
      buffer = null;
      continue;
    }
    buffer.push(line);
  }
  if (buffer !== null) blocks.push(buffer.join('\n'));
  return blocks;
}

/**
 * Find the full version of a diagram inside its original message.
 *
 * The rendered block is a prefix of the stored one (truncation only ever cuts
 * the tail), so a prefix probe identifies the right diagram when a message
 * carries several. Returns null rather than guessing — the caller then keeps
 * the partial content it already has.
 */
export function pickSvgBlockFor(fullContent: string, partial: string): string | null {
  const blocks = extractSvgBlocks(fullContent);
  if (blocks.length === 0) return null;
  if (blocks.length === 1) return blocks[0];
  const probe = partial.trimEnd().slice(0, 200);
  if (!probe) return null;
  return blocks.find(block => block.startsWith(probe)) ?? null;
}
