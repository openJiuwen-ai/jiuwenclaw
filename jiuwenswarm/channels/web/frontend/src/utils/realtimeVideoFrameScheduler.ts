export interface RealtimeVideoFrame {
  data_url: string;
  source_id: string;
}

/** Keeps the official ~1 fps aggregate cadence while rotating across sources. */
export class RealtimeVideoFrameScheduler {
  private lastSentAt = Number.NEGATIVE_INFINITY;
  private nextSourceIndex = 0;
  private readonly lastSentFrames = new Map<string, RealtimeVideoFrame>();

  constructor(private readonly minimumIntervalMs = 1_000) {}

  take<T extends RealtimeVideoFrame>(frames: readonly T[], now = Date.now()): T | null {
    if (now - this.lastSentAt < this.minimumIntervalMs) return null;

    const latestBySource = new Map<string, T>();
    frames.forEach(frame => latestBySource.set(frame.source_id, frame));
    const latest = [...latestBySource.values()];
    if (latest.length === 0) return null;

    for (let offset = 0; offset < latest.length; offset += 1) {
      const index = (this.nextSourceIndex + offset) % latest.length;
      const frame = latest[index];
      if (this.lastSentFrames.get(frame.source_id) === frame) continue;
      this.lastSentAt = now;
      this.nextSourceIndex = (index + 1) % latest.length;
      this.lastSentFrames.set(frame.source_id, frame);
      return frame;
    }
    return null;
  }
}
