function cleanModelText(text: string): string {
  return text
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<\/?think>/gi, '')
    .trim();
}

export function groundedSearchAnswer(text: string): string {
  const normalized = cleanModelText(text);
  if (!normalized.startsWith('九问检索摘要')) return normalized;
  const bodyStart = normalized.indexOf('\n\n');
  let body = bodyStart >= 0 ? normalized.slice(bodyStart + 2).trim() : normalized;
  const sourcesStart = body.lastIndexOf('\n\n来源：');
  if (sourcesStart >= 0) body = body.slice(0, sourcesStart).trim();
  return body
    .replace(/\s*\[来源\d+\]/g, '')
    .replace(/\*\*/g, '')
    .replace(/^\s*[-*]\s+/gm, '')
    .trim();
}

export function assistantSpeechText(text: string, maxChars = 180): string {
  const normalized = cleanModelText(text)
    .replace(/\[([^\]]+)]\(https?:\/\/[^)]+\)/g, '$1')
    .replace(/https?:\/\/\S+/g, '')
    .replace(/\s*\[来源\d+\]/g, '')
    .replace(/[*_#>`~]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
  if (normalized.length <= maxChars) return normalized;
  const prefix = normalized.slice(0, maxChars);
  const sentenceEnd = Math.max(
    prefix.lastIndexOf('。'),
    prefix.lastIndexOf('！'),
    prefix.lastIndexOf('？'),
  );
  if (sentenceEnd >= Math.floor(maxChars * 0.6)) return prefix.slice(0, sentenceEnd + 1);
  return `${prefix.replace(/[，、；：,.!?\s]+$/g, '')}。`;
}
