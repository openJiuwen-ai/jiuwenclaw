function cleanModelText(text: string): string {
  return text
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<\/?think>/gi, '')
    .trim();
}

interface SearchStatusItem {
  status: 'running' | 'queued' | 'failed';
}

export function searchAwareToolStatus(
  status: string,
  jobs: Iterable<SearchStatusItem>,
): string {
  const foreground = status.trim();
  const runningCount = [...jobs].filter((job) => job.status === 'running').length;
  if (runningCount === 0) return foreground;

  const background = `${runningCount} 项正在后台搜索，可继续提问…`;
  if (!foreground || /后台搜索|正在使用.+搜索/.test(foreground)) {
    return runningCount === 1 ? '正在后台搜索，可继续提问…' : background;
  }
  return `${foreground.replace(/[；。…]+$/u, '')}；另有 ${background}`;
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

const JOYAI_SEARCH_PROMPT_MAX_CHARS = 1_950;

function fitSearchEvidence(text: string, maxChars: number): string {
  const normalized = cleanModelText(text);
  if (normalized.length <= maxChars) return normalized;
  const separator = '\n...[中间内容因长度限制省略]...\n';
  const available = Math.max(0, maxChars - separator.length);
  const headChars = Math.floor(available * 0.35);
  return `${normalized.slice(0, headChars)}${separator}${normalized.slice(-(available - headChars))}`;
}

export function joyaiSearchAnswerInstruction(
  question: string,
  searchResult: string,
  retry = false,
): string {
  const normalizedQuestion = question.trim().slice(0, 400);
  const requirement = [
    '【本轮唯一任务】',
    '根据上面的已验证资料，直接回答原问题。资料中可能夹有搜索、抓取、重试或核实过程，这些只用于理解证据，禁止在回答中复述。',
    '只保留结论、必要依据和必要来源；不得声称将继续搜索，不得发起新的搜索。',
    '必须使用 `</response> 最终回答`，不得输出 `</silence>` 或 `</delegation>`。',
    retry ? '上一次没有产生有效最终回答；这一次必须直接给出可展示的最终回答。' : '',
  ].filter(Boolean).join('\n');
  const prefix = `【原问题】\n${normalizedQuestion}\n\n【Core Agent 已验证资料】\n`;
  const suffix = `\n\n${requirement}`;
  const evidenceBudget = Math.max(
    0,
    JOYAI_SEARCH_PROMPT_MAX_CHARS - prefix.length - suffix.length,
  );
  const evidence = fitSearchEvidence(searchResult, evidenceBudget);
  return `${prefix}${evidence}${suffix}`;
}

export function joyaiSearchFinalAnswer(result: {
  decision?: string;
  response?: string;
} | null | undefined): string {
  if (result?.decision !== 'response') return '';
  return cleanModelText(result.response || '');
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
