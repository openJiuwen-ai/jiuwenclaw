export type RetrievalPollingMode = "running" | "idle" | null;

export interface RetrievalPollingStatus {
  enabled?: boolean;
  build_status?: string;
}

export function getRetrievalPollingMode(
  activeTab: string,
  isActive: boolean,
  status: RetrievalPollingStatus | null,
): RetrievalPollingMode {
  if (!isActive || activeTab !== "index" || status?.enabled === false) {
    return null;
  }
  return status?.build_status === "running" ? "running" : "idle";
}
