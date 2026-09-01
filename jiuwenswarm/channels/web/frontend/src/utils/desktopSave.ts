export type DesktopSaveResult = {
  ok: boolean;
  cancelled?: boolean;
};

export type DesktopSaveApiResult = Promise<boolean | DesktopSaveResult> | boolean | DesktopSaveResult;

export type DesktopSaveOutcome = 'saved' | 'cancelled' | 'failed';

export function isDesktopSaveCancelled(result: boolean | DesktopSaveResult): boolean {
  return typeof result === 'object' && result.cancelled === true;
}

export function isDesktopSaveOk(result: boolean | DesktopSaveResult): boolean {
  return typeof result === 'boolean' ? result : result.ok;
}

export async function executeDesktopSave(save: () => DesktopSaveApiResult): Promise<DesktopSaveOutcome> {
  try {
    const result = await save();
    if (isDesktopSaveCancelled(result)) return 'cancelled';
    return isDesktopSaveOk(result) ? 'saved' : 'failed';
  } catch (error) {
    console.error('Desktop save API failed:', error);
    return 'failed';
  }
}
