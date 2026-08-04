/** 剥离 `//` 注释行，保存/校验 JSON 时使用。 */
export function stripJsonComments(text: string): string {
  return text
    .split('\n')
    .filter((line) => !line.trim().startsWith('//'))
    .join('\n')
    .trim();
}

/** 剥离示例注释行与旧版首行「示例…」标识。 */
export function stripExampleLabel(text: string): string {
  const withoutComments = stripJsonComments(text);
  const lines = withoutComments.split('\n');
  if (lines.length > 0 && lines[0].trim().startsWith('示例')) {
    return lines.slice(1).join('\n').trim();
  }
  return withoutComments;
}

export function withExampleComment(description: string, json: string): string {
  const trimmed = json.trim();
  const comment = `// ${description}：示例（请按实际情况修改）`;
  if (!trimmed) return comment;
  const firstLine = trimmed.split('\n')[0]?.trim() ?? '';
  if ((firstLine.startsWith('//') && firstLine.includes('示例')) || firstLine.startsWith('示例')) {
    return trimmed;
  }
  return `${comment}\n${trimmed}`;
}

export function isExamplePrefixed(text: string): boolean {
  const firstLine = text.trim().split('\n')[0]?.trim() ?? '';
  return (firstLine.startsWith('//') && firstLine.includes('示例')) || firstLine.startsWith('示例');
}

export function jsonContentForValidation(text: string): string {
  return stripExampleLabel(text);
}
