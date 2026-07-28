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
