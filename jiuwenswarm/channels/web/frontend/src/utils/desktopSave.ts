export type DesktopSaveResult = {
  ok: boolean;
  cancelled?: boolean;
};

export type DesktopSaveApiResult = Promise<boolean | DesktopSaveResult> | boolean | DesktopSaveResult;

export function isDesktopSaveCancelled(result: boolean | DesktopSaveResult): boolean {
  return typeof result === 'object' && result.cancelled === true;
}

export function isDesktopSaveOk(result: boolean | DesktopSaveResult): boolean {
  return typeof result === 'boolean' ? result : result.ok;
}
