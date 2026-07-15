import type { Message } from '../types';

function findLatestUserIndex(messages: Message[]): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].role === 'user') {
      return index;
    }
  }
  return -1;
}

function isTeamLeaderMessage(message: Message): boolean {
  return message.id.startsWith('team-leader-');
}

export function findActiveTeamLeaderMessage(messages: Message[]): Message | undefined {
  const latestUserIndex = findLatestUserIndex(messages);
  for (let index = messages.length - 1; index > latestUserIndex; index -= 1) {
    const message = messages[index];
    if (isTeamLeaderMessage(message) && message.isStreaming) {
      return message;
    }
  }
  return undefined;
}
