import { useMemo } from 'react';
import { Message } from '../../types';
import {
  parseTeamEventMessage,
  ParsedTeamEvent,
} from './teamEventUtils';
import { formatMemberName, MarkdownMessageBody, TeamMemberAvatar } from './MessageItem';
import teamIcon from '../../assets/team.svg';

interface TeamEventGroupDisplayProps {
  messages: Message[];
  showAvatar?: boolean;
}

function isTeamLeaderDisplayName(member: string) {
  return formatMemberName(member) === 'TeamLeader';
}

function buildClusterTitleMembers(members: string[]) {
  const sortedMembers = [...members].sort((a, b) => {
    if (isTeamLeaderDisplayName(a)) return -1;
    if (isTeamLeaderDisplayName(b)) return 1;
    return a.localeCompare(b, undefined, { numeric: true });
  });
  const displayNames = sortedMembers.map(formatMemberName);

  if (displayNames.length <= 3) {
    return displayNames.join('、');
  }

  return `${displayNames.slice(0, 2).join('、')} 等${displayNames.length}人`;
}

function TeamEventRow({
  event,
}: {
  event: ParsedTeamEvent;
}) {
  return (
    <div className="team-event-group-row">
      <div className="team-event-group-row__avatar">
        <TeamMemberAvatar member={event.fromMember} />
      </div>
      <div className="team-event-group-row__main">
        <div className="team-event-group-row__meta">
          <span className="team-event-group-row__member">
            {formatMemberName(event.fromMember)}
          </span>
        </div>
        <div className="team-event-group-row__content">
          {event.isP2P && event.toMember && (
            <span className="team-event-group-chip team-event-group-chip--p2p">
              @{event.toMember}
            </span>
          )}
          {event.isBroadcast && (
            <span className="team-event-group-chip team-event-group-chip--broadcast">
              @所有人
            </span>
          )}
          <MarkdownMessageBody
            content={event.content}
            className="team-message-markdown team-message-markdown--inline"
          />
        </div>
      </div>
    </div>
  );
}

export function TeamEventGroupDisplay({
  messages,
  showAvatar = true,
}: TeamEventGroupDisplayProps) {
  const events = useMemo(
    () => messages
      .map(parseTeamEventMessage)
      .filter((event): event is ParsedTeamEvent => !!event),
    [messages]
  );

  if (events.length === 0) {
    return null;
  }

  const members = Array.from(
    new Set(events.map((event) => event.fromMember).filter(Boolean))
  );
  const clusterTitleMembers = buildClusterTitleMembers(members);

  return (
    <div className="team-event-group-shell animate-rise" data-testid="team-event-group">
      <div className="team-event-group-shell__spacer">
        {showAvatar ? <TeamMemberAvatar member="team_leader" /> : null}
      </div>
      <div className="team-event-group">
        <div className="team-event-group-summary">
          <div className="team-event-group-summary__main">
            <span className="team-event-group-summary__icon" aria-hidden="true">
              <img src={teamIcon} alt="" />
            </span>
            <span className="team-event-group-summary__title">
              集群
            </span>
            <span className="team-event-group-summary__separator" aria-hidden="true">
              |
            </span>
            <span className="team-event-group-summary__meta">
              {clusterTitleMembers}
            </span>
          </div>
        </div>
        <div className="team-event-group-list">
          {events.map((event, index) => (
            <TeamEventRow
              key={`${event.fromMember}-${event.timestamp ?? index}-${index}`}
              event={event}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
