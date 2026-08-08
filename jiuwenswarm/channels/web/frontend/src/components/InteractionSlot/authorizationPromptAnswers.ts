import type { Question, UserAnswer } from '../../types';
import { classifyAuthOption, type AuthSemantic } from './promptRouting';

type TranslateFn = (key: string) => string;

const AUTH_LABEL_I18N: Partial<Record<AuthSemantic, string>> = {
  'allow-once': 'authPrompt.allowOnce',
  'allow-always': 'authPrompt.allowAlways',
  'session-allow': 'authPrompt.sessionAllow',
  reject: 'authPrompt.reject',
};

const AUTH_TIP_I18N: Partial<Record<AuthSemantic, string>> = {
  'allow-once': 'authPrompt.tip.allowOnce',
  'allow-always': 'authPrompt.tip.allowAlways',
  'session-allow': 'authPrompt.tip.sessionAllow',
  reject: 'authPrompt.tip.reject',
};

/** Display label follows the user's UI language; payload values stay backend-native. */
export function resolveAuthActionLabel(
  semantic: AuthSemantic,
  backendLabel: string,
  t: TranslateFn,
): string {
  const key = AUTH_LABEL_I18N[semantic];
  return key ? t(key) : backendLabel;
}

export function resolveAuthActionTip(
  semantic: AuthSemantic,
  backendTip: string,
  t: TranslateFn,
): string {
  const key = AUTH_TIP_I18N[semantic];
  return key ? t(key) : backendTip;
}

export interface DenyDraft {
  mode: boolean;
  note: string;
}

export type DenyDraftAction =
  | { type: 'open' }
  | { type: 'update-note'; note: string }
  | { type: 'reset' };

export const INITIAL_DENY_DRAFT: DenyDraft = {
  mode: false,
  note: '',
};

export function reduceDenyDraft(state: DenyDraft, action: DenyDraftAction): DenyDraft {
  switch (action.type) {
    case 'open':
      return { ...state, mode: true };
    case 'update-note':
      return { ...state, note: action.note };
    case 'reset':
      return INITIAL_DENY_DRAFT;
  }
}

/**
 * 与 TUI 的 shouldCollectPermissionDenyFeedback 对齐：仅工具权限（permission_interrupt）
 * 和操作确认（confirm_interrupt）在拒绝时弹出备注输入；其余 source（activate_confirm、
 * evolution_interrupt 等）保持旧行为——点「拒绝」立即提交，不收集备注。
 */
export function canCollectDenyFeedback(
  source: string | undefined,
  planApprovalKind?: string,
): boolean {
  if (planApprovalKind === 'plan_approval') return false;
  return source === 'permission_interrupt' || source === 'confirm_interrupt';
}

export function handleDenyEscape(
  event: Pick<KeyboardEvent, 'key' | 'preventDefault'>,
  cancelDeny: () => void,
): void {
  if (event.key !== 'Escape') return;
  event.preventDefault();
  cancelDeny();
}

export function buildDenyAnswers(questions: Question[], feedback: string): UserAnswer[] {
  const customInput = feedback.trim();

  return questions.map((question) => {
    const rejectOption =
      question.options.find(
        (option) => classifyAuthOption(option.value || option.label) === 'reject',
      ) ||
      question.options.find((option) => (option.label || '').includes('拒绝'));
    const value = rejectOption ? rejectOption.value || rejectOption.label : '拒绝';

    return {
      selected_options: [value],
      custom_input: customInput,
    };
  });
}
