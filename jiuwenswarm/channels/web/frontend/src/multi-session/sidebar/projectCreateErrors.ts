export function projectCreateErrorKey(error: unknown): string | null {
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes('project_path already exists')) {
    return 'multiSession.project.errors.pathExists';
  }
  return null;
}
