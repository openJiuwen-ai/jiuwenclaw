import { forwardRef, useMemo } from 'react';
import { toPng } from 'html-to-image';
import { useTranslation } from 'react-i18next';
import { ChatTimelineList } from '../components/ChatPanel/MessageList';
import { MarkdownMessageBody } from '../components/ChatPanel/MessageItem';
import { TeamMemberAvatar } from '../components/TeamMemberAvatar';
import { getMemberDisplayName } from '../components/teamArea/shared';
import {
  formatTeamEventTime,
  parseTeamEventMessage,
  type ParsedTeamEvent,
} from '../components/ChatPanel/teamEventUtils';
import { isUserMember } from '../utils/teamMemberAvatar';
import { parseHistoryJsonFileToPreviewMessages } from './historyRestore';
import { parseTeamHistoryPanelRecords } from './teamHistoryPanelRestore';
import './shareImageExport.css';

export interface ShareImageMetadata {
  title?: string;
  exported_at?: string;
  filename?: string;
}

export interface ShareImageSnapshot {
  session_id: string;
  metadata?: ShareImageMetadata;
  records: unknown[];
}

interface ShareImageDocumentProps {
  snapshot: ShareImageSnapshot | null;
}

interface GroupMessage {
  event: ParsedTeamEvent;
  timestampMs: number;
}

const SHARE_IMAGE_WIDTH = 750;
const SHARE_IMAGE_PIXEL_RATIO = 3;
const OPENJIUWEN_WEBSITE_URL = 'https://openjiuwen.com';
const JIUWENSWARM_REPO_URL = 'https://gitcode.com/openJiuwen/jiuwenswarm';

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function normalizeMode(records: unknown[]): string {
  const modes = records
    .filter(isRecord)
    .map((record) => typeof record.mode === 'string' ? record.mode.trim().toLowerCase() : '')
    .filter(Boolean);
  return modes.includes('team') ? 'team' : modes[0] || 'agent.plan';
}

function readableDate(value?: string): string {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function collectGroupMessages(snapshot: ShareImageSnapshot): GroupMessage[] {
  const state = parseTeamHistoryPanelRecords(snapshot.records, snapshot.session_id);
  const items: GroupMessage[] = [];

  for (const message of state.messages) {
    const event = parseTeamEventMessage(message);
    if (!event || event.isLeaderToUser) {
      continue;
    }
    items.push({
      event,
      timestampMs: event.timestamp || Date.parse(message.timestamp) || 0,
    });
  }

  return items.sort((a, b) => a.timestampMs - b.timestampMs);
}

function GroupChatMessage({ item }: { item: GroupMessage }) {
  const { t } = useTranslation();
  const { event } = item;
  const isUser = isUserMember(event.fromMember);
  const displayName = getMemberDisplayName(event.fromMember);
  const timeText = formatTeamEventTime(event.timestamp);

  return (
    <article className={`share-image-group-message ${isUser ? 'is-user' : ''}`}>
      {!isUser && (
        <TeamMemberAvatar
          member={event.fromMember}
          className="share-image-group-message__avatar"
        />
      )}
      <div className="share-image-group-message__main">
        <div className="share-image-group-message__meta">
          <span className="share-image-group-message__member">{displayName}</span>
          {timeText && <span className="share-image-group-message__time">{timeText}</span>}
        </div>
        <div className="share-image-group-message__bubble">
          {event.isP2P && event.toMember && (
            <span className="share-image-group-message__chip">@{getMemberDisplayName(event.toMember)}</span>
          )}
          {event.isBroadcast && (
            <span className="share-image-group-message__chip">{t('share.everyone')}</span>
          )}
          <MarkdownMessageBody
            content={event.content}
            className="share-image-group-message__body"
          />
        </div>
      </div>
      {isUser && (
        <TeamMemberAvatar
          member={event.fromMember}
          className="share-image-group-message__avatar"
        />
      )}
    </article>
  );
}

export const ShareImageDocument = forwardRef<HTMLDivElement, ShareImageDocumentProps>(
  function ShareImageDocument({ snapshot }, ref) {
    const { t } = useTranslation();
    const data = useMemo(() => {
      if (!snapshot) {
        return null;
      }
      const messages = parseHistoryJsonFileToPreviewMessages(snapshot.records, snapshot.session_id);
      return {
        mode: normalizeMode(snapshot.records),
        messages,
        groupMessages: collectGroupMessages(snapshot),
      };
    }, [snapshot]);

    if (!snapshot || !data) {
      return <div ref={ref} className="share-image-document" />;
    }

    const title = snapshot.metadata?.title?.trim() || snapshot.session_id;
    const exportedAt = readableDate(snapshot.metadata?.exported_at);
    const hasConversation = data.messages.length > 0;
    const isTeamMode = data.mode === 'team';
    const hasGroupMessages = data.groupMessages.length > 0;

    return (
      <div ref={ref} className="share-image-document">
        <header className="share-image-header">
          <div className="share-image-masthead">
            <div className="share-image-brand">
              <img src="/logo.svg" alt="" className="share-image-brand__logo" />
              <div className="share-image-brand__name">JiuwenSwarm</div>
            </div>
          </div>
        </header>

        <main className="share-image-content">
          <div className="share-image-content-header">
            <h1>{title}</h1>
            <div className="share-image-meta">
              <span>{snapshot.session_id}</span>
              {exportedAt && <span>{exportedAt}</span>}
            </div>
          </div>

          <section className="share-image-section">
            <div className="share-image-section__label">{t('share.mainConversation')}</div>
            {hasConversation ? (
              <ChatTimelineList
                messages={data.messages}
                executions={[]}
                mode={data.mode}
              />
            ) : (
              <div className="share-image-empty">{t('share.noMainConversation')}</div>
            )}
          </section>

          {isTeamMode && (
            <section className="share-image-section share-image-section--group">
              <div className="share-image-section__label">{t('share.groupChat')}</div>
              {hasGroupMessages ? (
                <div className="share-image-group-list">
                  {data.groupMessages.map((item) => (
                    <GroupChatMessage key={item.event.messageId} item={item} />
                  ))}
                </div>
              ) : (
                <div className="share-image-empty">{t('share.noGroupChat')}</div>
              )}
            </section>
          )}
        </main>

        <footer className="share-image-footer">
          <div className="share-image-footer__note">{t('share.generatedBy')}</div>
          <div className="share-image-links">
            <div className="share-image-link">
              <span>{t('share.website', { url: OPENJIUWEN_WEBSITE_URL })}</span>
            </div>
            <div className="share-image-link-divider" />
            <div className="share-image-link">
              <span>{t('share.repository', { url: JIUWENSWARM_REPO_URL })}</span>
            </div>
          </div>
        </footer>
      </div>
    );
  }
);

function nextFrame(): Promise<void> {
  return new Promise((resolve) => {
    requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
  });
}

async function waitForImages(node: HTMLElement): Promise<void> {
  const images = Array.from(node.querySelectorAll('img'));
  await Promise.all(images.map(async (image) => {
    if (image.complete && image.naturalWidth > 0) {
      return;
    }
    if (typeof image.decode === 'function') {
      await image.decode();
      return;
    }
    await new Promise<void>((resolve, reject) => {
      image.addEventListener('load', () => resolve(), { once: true });
      image.addEventListener('error', () => reject(new Error('share_image_asset_failed')), { once: true });
    });
  }));
}

export async function exportShareImageNode(node: HTMLElement): Promise<string> {
  await document.fonts?.ready;
  await waitForImages(node);
  await nextFrame();
  const backgroundColor = window.getComputedStyle(node).backgroundColor;
  return toPng(node, {
    cacheBust: true,
    pixelRatio: SHARE_IMAGE_PIXEL_RATIO,
    width: SHARE_IMAGE_WIDTH,
    height: node.scrollHeight,
    backgroundColor,
  });
}
