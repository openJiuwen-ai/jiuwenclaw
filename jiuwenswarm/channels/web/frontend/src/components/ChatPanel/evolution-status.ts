import type { AgentMode } from '../../types/index.ts';
import type { EvolutionStatusPayload } from '../../types/websocket.ts';

type Translate = (key: string) => string;

const STAGE_KEY_MAP: Record<string, string> = {
  collecting: 'statusBar.evolutionStages.collecting',
  detecting: 'statusBar.evolutionStages.detecting',
  generating: 'statusBar.evolutionStages.generating',
  awaiting_approval: 'statusBar.evolutionStages.awaitingApproval',
  approval_required: 'statusBar.evolutionStages.awaitingApproval',
  no_evolution_generated: 'statusBar.evolutionStages.noEvolutionGenerated',
  completed: 'statusBar.evolutionStages.completed',
  timed_out: 'statusBar.evolutionStages.timedOut',
  failed: 'statusBar.evolutionStages.failed',
};

export function getEvolutionPillLabel(
  mode: AgentMode,
  evolutionStatus: EvolutionStatusPayload | null,
  t: Translate,
): string | null {
  if (!evolutionStatus) {
    return null;
  }

  const stage = (evolutionStatus.stage || '').trim().toLowerCase();
  if (stage === 'hidden' || (mode !== 'team' && stage === 'failed')) {
    return null;
  }
  const translationKey = STAGE_KEY_MAP[stage];
  if (translationKey) {
    return t(translationKey);
  }

  const message = typeof evolutionStatus.message === 'string' ? evolutionStatus.message.trim() : '';
  return message || t('statusBar.evolving');
}
