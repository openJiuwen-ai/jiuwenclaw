export type RealtimeVideoSource = 'camera' | 'file' | 'screen' | null;

export interface RealtimeScreenSource {
  id: string;
}

interface VideoSourceReadiness {
  source: RealtimeVideoSource;
  cameraStream: MediaStream | null;
  screens: readonly RealtimeScreenSource[];
  screenStreams: ReadonlyMap<string, MediaStream>;
  video: HTMLMediaElement | null;
}

interface FirstFrameWaitOptions {
  timeoutMs?: number;
  pollMs?: number;
  now?: () => number;
  wait?: (delayMs: number) => Promise<void>;
}

function hasLiveVideoTrack(stream: MediaStream | null | undefined): boolean {
  return Boolean(stream?.getVideoTracks().some((track) => track.readyState === 'live'));
}

export function isVideoSourceReady({
  source,
  cameraStream,
  screens,
  screenStreams,
  video,
}: VideoSourceReadiness): boolean {
  if (source === 'camera') return hasLiveVideoTrack(cameraStream);
  if (source === 'screen') {
    return screens.length > 0
      && screens.every((screen) => hasLiveVideoTrack(screenStreams.get(screen.id)));
  }
  if (source === 'file') return Boolean(video && !video.paused);
  return true;
}

export async function waitForFirstVideoFrame(
  isSourceReady: () => boolean,
  hasFrame: () => boolean,
  {
    timeoutMs = 5_000,
    pollMs = 50,
    now = Date.now,
    wait = (delayMs) => new Promise((resolve) => window.setTimeout(resolve, delayMs)),
  }: FirstFrameWaitOptions = {},
): Promise<boolean> {
  const deadline = now() + timeoutMs;
  while (isSourceReady() && !hasFrame() && now() < deadline) await wait(pollMs);
  return isSourceReady() && hasFrame();
}
