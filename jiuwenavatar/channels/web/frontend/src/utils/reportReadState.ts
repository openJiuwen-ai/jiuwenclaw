import { webRequest } from '../services/webClient';
import { enterpriseUserContext } from './enterpriseContext';

export interface ViewRecord {
  viewedAt: string;
  status?: string;
}

export interface ReportReadState {
  missions: Record<string, ViewRecord>;
  reports: Record<string, ViewRecord>;
}

const LEGACY_STORAGE_KEY = 'jiuwenavatar.reportReadState';

export async function fetchReportReadState(): Promise<ReportReadState> {
  const res = await webRequest<{ read_state?: ReportReadState }>('report_read_state.get', {});
  return res?.read_state ?? { missions: {}, reports: {} };
}

export async function persistReportReadState(readState: ReportReadState): Promise<void> {
  await webRequest('report_read_state.set', { read_state: readState });
}

export async function fetchUnreadCountsByAvatar(): Promise<Record<string, number>> {
  const res = await webRequest<{ missions_by_avatar?: Record<string, number> }>(
    'report.unread_counts',
    { ...enterpriseUserContext() },
  );
  return res?.missions_by_avatar ?? {};
}

/** 将旧版 localStorage 已读状态迁移到服务端（一次性）。 */
export async function migrateLegacyReadStateIfNeeded(): Promise<ReportReadState> {
  let serverState = await fetchReportReadState();
  try {
    const raw = localStorage.getItem(LEGACY_STORAGE_KEY);
    if (!raw) {
      return serverState;
    }
    const legacy = JSON.parse(raw) as Partial<ReportReadState>;
    if (legacy && typeof legacy === 'object') {
      const merged: ReportReadState = {
        missions: { ...(legacy.missions || {}), ...serverState.missions },
        reports: { ...(legacy.reports || {}), ...serverState.reports },
      };
      if (Object.keys(merged.missions).length || Object.keys(merged.reports).length) {
        serverState = merged;
        await persistReportReadState(serverState);
      }
    }
    localStorage.removeItem(LEGACY_STORAGE_KEY);
  } catch {
    /* ignore corrupt legacy data */
  }
  return serverState;
}
