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
