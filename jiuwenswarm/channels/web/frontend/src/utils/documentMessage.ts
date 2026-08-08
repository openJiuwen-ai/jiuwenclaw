/** Agent-facing hint block listing this turn's uploaded documents. */
export const UPLOAD_DOCUMENT_BLOCK_HEADER = '【上传文档】';

export interface UploadDocumentHint {
  filename: string;
  path?: string;
  /**
   * Source file when `path` points at a generated sidecar (e.g. the .txt
   * extracted from a PDF). Listed alongside so the model can still reach the
   * binary for page-level tools such as read_pdf.
   */
  originalPath?: string;
}

/** Strip agent document-hint blocks from user-visible bubble text. */
export function stripUploadDocumentBlocks(content: string): string {
  if (!content || !content.includes('【上传文档')) {
    return content;
  }
  return content
    // New compact form: 【上传文档】 plus following "- name: path" lines.
    .replace(/(?:^|\n+)【上传文档】(?:\n-[^\n]*)*/g, '')
    // Legacy per-file blocks: 【上传文档: name】 / 路径 / 说明
    .replace(/(?:^|\n+)【上传文档[:：][^\n]*(?:\n(?!【)[^\n]*)*/g, '')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

/**
 * Append the agent-facing document hint block to `text`.
 *
 * Any block already present is stripped first, so calling this again with
 * freshly persisted records replaces filename-only lines with real paths.
 * Documents without a path still get listed — the agent at least learns the
 * file exists and can ask for it.
 */
export function withUploadDocumentBlock(text: string, docs: UploadDocumentHint[]): string {
  const base = stripUploadDocumentBlocks(text);
  if (!docs.length) {
    return base;
  }
  const lines = docs.map((doc) => {
    if (!doc.path) return `- ${doc.filename}`;
    if (doc.originalPath && doc.originalPath !== doc.path) {
      return `- ${doc.filename}: ${doc.path} (original file: ${doc.originalPath})`;
    }
    return `- ${doc.filename}: ${doc.path}`;
  });
  return [base, UPLOAD_DOCUMENT_BLOCK_HEADER, ...lines].filter(Boolean).join('\n');
}

function readString(value: unknown): string | undefined {
  return typeof value === 'string' && value.trim() ? value : undefined;
}

/** Extract document hints from persisted `media_items` returned by document.persist. */
export function toUploadDocumentHints(mediaItems: unknown): UploadDocumentHint[] {
  if (!Array.isArray(mediaItems)) {
    return [];
  }
  const hints: UploadDocumentHint[] = [];
  for (const item of mediaItems) {
    if (!item || typeof item !== 'object') continue;
    const record = item as Record<string, unknown>;
    if (record.type !== 'document') continue;
    const path = readString(record.path);
    const filename = readString(record.filename) || (path ? path.split(/[\\/]/).pop() : undefined);
    if (!filename) continue;
    hints.push({ filename, path, originalPath: readString(record.original_path) });
  }
  return hints;
}
