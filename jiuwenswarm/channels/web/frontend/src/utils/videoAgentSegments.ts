export interface VideoAgentSegment {
  order: number;
  text: string;
  realtimeAnswer: string;
  requestVersion: number;
}

export interface VideoAgentTurn {
  version: number;
  question: string;
  realtimeAnswer: string;
}

export type VoiceTranscriptSource = 'native' | 'local';

export interface VoiceTranscriptRouteStamp {
  key: string;
  source: VoiceTranscriptSource;
  routedAt: number;
}

export function evaluateVoiceTranscriptRoute(
  previous: VoiceTranscriptRouteStamp | null,
  transcript: string,
  source: VoiceTranscriptSource,
  routedAt = Date.now(),
): { route: boolean; stamp: VoiceTranscriptRouteStamp } {
  const key = transcript.normalize('NFKC').toLocaleLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
  const stamp = { key, source, routedAt };
  const crossSourceDuplicate = Boolean(
    key
    && previous?.key === key
    && previous.source !== source
    && routedAt - previous.routedAt <= 15_000,
  );
  return { route: Boolean(key) && !crossSourceDuplicate, stamp };
}

function isUsefulTranscript(text: string): boolean {
  return text.replace(/[^\p{L}\p{N}]/gu, '').length >= 2;
}

export function advanceMeaningfulVideoAgentVersion(
  currentVersion: number,
  candidateVersion: number,
  transcript: string,
): number {
  return isUsefulTranscript(transcript)
    ? Math.max(currentVersion, candidateVersion)
    : currentVersion;
}

export function collectVideoAgentTurns(segments: VideoAgentSegment[]): VideoAgentTurn[] {
  const groups = new Map<number, VideoAgentSegment[]>();
  [...segments]
    .sort((left, right) => left.order - right.order)
    .forEach((segment) => {
      const group = groups.get(segment.requestVersion) || [];
      group.push(segment);
      groups.set(segment.requestVersion, group);
    });

  return [...groups.entries()].flatMap(([version, group]) => {
    const question = group
      .map((segment) => segment.text.trim())
      .filter(isUsefulTranscript)
      .filter((text, index, values) => text !== values[index - 1])
      .join('。');
    if (!question) return [];
    const realtimeAnswer = [...group]
      .reverse()
      .find((segment) => segment.realtimeAnswer.trim())
      ?.realtimeAnswer.trim() || '';
    return [{ version, question, realtimeAnswer }];
  });
}
