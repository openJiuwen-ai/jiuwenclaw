/**
 * Quiet inline rail for AgentDropout audit notices in the chat stream.
 * Replaces the default system "pill" for pruning events.
 */

import { useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import clsx from 'clsx';
import { ChevronDown, ChevronRight, ShieldCheck } from 'lucide-react';

export type AgentDropoutPhase =
  | 'check'
  | 'pass'
  | 'rectify'
  | 'reject'
  | 'drop'
  | 'blocked'
  | 'error'
  | 'unknown';

const PHASE_FROM_ID: Record<string, AgentDropoutPhase> = {
  agent_dropout_check: 'check',
  agent_dropout_pass: 'pass',
  agent_dropout_rectify: 'rectify',
  agent_dropout_reject: 'reject',
  agent_dropout_drop: 'drop',
  agent_dropout_blocked: 'blocked',
  agent_dropout_error: 'error',
};

export function parseAgentDropoutPhase(messageId: string | undefined): AgentDropoutPhase | null {
  if (!messageId?.startsWith('notice-agent_dropout')) {
    return null;
  }
  // notice-agent_dropout_pass-<requestId>
  const withoutPrefix = messageId.slice('notice-'.length);
  const matched = Object.keys(PHASE_FROM_ID).find((key) => withoutPrefix.startsWith(key));
  return matched ? PHASE_FROM_ID[matched] : 'unknown';
}

export function isAgentDropoutNotice(messageId: string | undefined): boolean {
  return parseAgentDropoutPhase(messageId) !== null;
}

function extractMemberName(content: string): string | undefined {
  const quoted = content.match(/'([^']+)'/);
  if (quoted?.[1]) return quoted[1];
  const member = content.match(/member\s+'([^']+)'/i);
  return member?.[1];
}

function cleanBody(content: string): string {
  return content
    .replace(/^AgentDropout:\s*/i, '')
    .replace(/^\[AGENT_DROPOUT\]\s*/i, '')
    .trim();
}

interface AgentDropoutNoticeProps {
  messageId: string;
  content: string;
}

export function AgentDropoutNotice({ messageId, content }: AgentDropoutNoticeProps) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState(false);
  const phase = parseAgentDropoutPhase(messageId) ?? 'unknown';
  const member = useMemo(() => extractMemberName(content), [content]);
  const body = useMemo(() => cleanBody(content), [content]);
  const isQuiet = phase === 'check' || phase === 'pass';
  const summary = useMemo(() => {
    const oneLine = body.replace(/\s+/g, ' ');
    if (oneLine.length <= 96) return oneLine;
    return `${oneLine.slice(0, 95)}…`;
  }, [body]);

  const phaseLabel = t(`chatUi.agentDropout.phases.${phase}`, {
    defaultValue: phase,
  });

  return (
    <div
      className={clsx(
        'agent-dropout-notice animate-fade-in',
        `agent-dropout-notice--${phase}`,
        isQuiet && 'agent-dropout-notice--quiet'
      )}
      data-testid="agent-dropout-notice"
      data-phase={phase}
    >
      <button
        type="button"
        className="agent-dropout-notice__row"
        onClick={() => setExpanded((v) => !v)}
        aria-expanded={expanded}
      >
        <span className="agent-dropout-notice__rail" aria-hidden />
        <ShieldCheck className="agent-dropout-notice__icon" strokeWidth={1.75} />
        <span className="agent-dropout-notice__meta">
          <span className="agent-dropout-notice__brand">
            {t('chatUi.agentDropout.brand', { defaultValue: 'Pruning' })}
          </span>
          <span className="agent-dropout-notice__dot" aria-hidden>
            ·
          </span>
          <span className="agent-dropout-notice__phase">{phaseLabel}</span>
          {member ? (
            <>
              <span className="agent-dropout-notice__dot" aria-hidden>
                ·
              </span>
              <span className="agent-dropout-notice__member">{member}</span>
            </>
          ) : null}
        </span>
        <span className="agent-dropout-notice__summary">{summary}</span>
        <span className="agent-dropout-notice__chevron" aria-hidden>
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>
      {expanded ? (
        <pre className="agent-dropout-notice__detail">{body}</pre>
      ) : null}
    </div>
  );
}
