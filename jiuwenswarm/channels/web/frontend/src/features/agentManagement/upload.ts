const AGENT_ARCHIVE_EXTENSIONS = ['.zip', '.tar'] as const;

export function isAgentUploadFilename(name: string): boolean {
  const lower = name.toLowerCase();
  return AGENT_ARCHIVE_EXTENSIONS.some(extension => lower.endsWith(extension));
}
