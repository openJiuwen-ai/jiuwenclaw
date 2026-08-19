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
