export function projectCreateErrorKey(error: unknown): string | null {
  const message = error instanceof Error ? error.message : String(error);
  if (message.includes('project_dir already exists')) {
    return 'multiSession.project.errors.pathExists';
  }
  if (message.includes('project name already exists')) {
    return 'multiSession.project.errors.nameExists';
  }
  return null;
}
