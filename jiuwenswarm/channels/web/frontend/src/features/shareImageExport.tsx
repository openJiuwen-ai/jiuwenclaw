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
import { isA2UIClientEventContent } from './a2ui/a2uiContent';
import { getSvgNaturalHeight, getSvgNaturalWidth } from '../utils/svgDimensions';
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
const TRANSPARENT_IMAGE_DATA_URL = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw==';

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

/**
 * Filter out A2UI client event messages from the message list.
 * These messages are internal interaction events and should not be included in exports.
 */
function filterA2UIClientEvents(messages: unknown[]): unknown[] {
  return messages.filter((msg) => {
    if (!isRecord(msg)) return true;
    if (msg.role === 'user' && isA2UIClientEventContent(msg.content)) return false;
    return true;
  });
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
      // Filter out A2UI client event messages from exports
      const filteredMessages = filterA2UIClientEvents(messages) as typeof messages;
      return {
        mode: normalizeMode(snapshot.records),
        messages: filteredMessages,
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
                disableA2UIInteraction={true}
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

interface ImageSnapshot {
  image: HTMLImageElement;
  src: string | null;
  srcset: string | null;
  sizes: string | null;
}

function replaceBrokenImageForExport(image: HTMLImageElement, snapshots: ImageSnapshot[]): void {
  snapshots.push({
    image,
    src: image.getAttribute('src'),
    srcset: image.getAttribute('srcset'),
    sizes: image.getAttribute('sizes'),
  });
  image.removeAttribute('srcset');
  image.removeAttribute('sizes');
  image.src = TRANSPARENT_IMAGE_DATA_URL;
}

async function waitForImage(image: HTMLImageElement): Promise<boolean> {
  if (image.complete) {
    return image.naturalWidth > 0;
  }
  if (typeof image.decode === 'function') {
    await image.decode();
    return image.naturalWidth > 0;
  }
  return new Promise<boolean>((resolve) => {
    image.addEventListener('load', () => resolve(image.naturalWidth > 0), { once: true });
    image.addEventListener('error', () => resolve(false), { once: true });
  });
}

async function prepareImagesForExport(node: HTMLElement): Promise<() => void> {
  const images = Array.from(node.querySelectorAll('img'));
  const snapshots: ImageSnapshot[] = [];

  await Promise.all(images.map(async (image) => {
    try {
      if (await waitForImage(image)) {
        return;
      }
    } catch {
      // Ignore broken or undecodable images in share export. A2UI Image can
      // intentionally contain an invalid URL to demonstrate fallback UI.
    }

    replaceBrokenImageForExport(image, snapshots);
    try {
      await waitForImage(image);
    } catch {
      // The transparent data URL should decode, but keep export tolerant.
    }
  }));

  return () => {
    for (const snapshot of snapshots) {
      const { image, src, srcset, sizes } = snapshot;
      if (src === null) image.removeAttribute('src');
      else image.setAttribute('src', src);
      if (srcset === null) image.removeAttribute('srcset');
      else image.setAttribute('srcset', srcset);
      if (sizes === null) image.removeAttribute('sizes');
      else image.setAttribute('sizes', sizes);
    }
  };
}

interface SvgSnapshot {
  svg: SVGSVGElement;
  width: string | null;
  height: string | null;
  styleWidth: string;
  styleHeight: string;
  styleMaxWidth: string;
}

/**
 * Scales down any Mermaid SVG that is wider than its container so the full
 * diagram fits inside the share image without being clipped horizontally.
 * Returns a cleanup function that restores the original attributes/styles.
 */
function fitMermaidDiagramsForExport(node: HTMLElement): () => void {
  const svgs = Array.from(node.querySelectorAll<SVGSVGElement>('.share-image-document .mermaid-canvas svg'));
  const snapshots: SvgSnapshot[] = [];

  for (const svg of svgs) {
    const naturalWidth = getSvgNaturalWidth(svg);
    const naturalHeight = getSvgNaturalHeight(svg);
    if (naturalWidth <= 0 || naturalHeight <= 0) continue;

    const container = svg.closest<HTMLElement>('.mermaid-canvas') ?? svg.parentElement;
    const containerWidth = container?.clientWidth ?? 0;
    if (containerWidth <= 0 || naturalWidth <= containerWidth) continue;

    const ratio = containerWidth / naturalWidth;
    snapshots.push({
      svg,
      width: svg.getAttribute('width'),
      height: svg.getAttribute('height'),
      styleWidth: svg.style.width,
      styleHeight: svg.style.height,
      styleMaxWidth: svg.style.maxWidth,
    });

    svg.setAttribute('width', String(containerWidth));
    svg.setAttribute('height', String(naturalHeight * ratio));
    svg.style.width = `${containerWidth}px`;
    svg.style.height = `${naturalHeight * ratio}px`;
    svg.style.maxWidth = 'none';
  }

  return () => {
    for (const snapshot of snapshots) {
      const { svg, width, height, styleWidth, styleHeight, styleMaxWidth } = snapshot;
      if (width === null) svg.removeAttribute('width');
      else svg.setAttribute('width', width);
      if (height === null) svg.removeAttribute('height');
      else svg.setAttribute('height', height);
      svg.style.width = styleWidth;
      svg.style.height = styleHeight;
      svg.style.maxWidth = styleMaxWidth;
    }
  };
}

async function waitForMermaidDiagrams(node: HTMLElement): Promise<void> {
  function assertNoFailedDiagrams(): void {
    if (node.querySelector('[data-mermaid-status="error"]')) {
      throw new Error('share_image_mermaid_render_failed');
    }
  }

  function hasPendingDiagrams(): boolean {
    return node.querySelector('[data-mermaid-status="loading"]') !== null;
  }

  function allRenderedDiagramsHaveSvg(): boolean {
    return Array.from(node.querySelectorAll('[data-mermaid-status="rendered"]'))
      .every((diagram) => diagram.querySelector('svg'));
  }

  function isReady(): boolean {
    assertNoFailedDiagrams();
    return !hasPendingDiagrams() && allRenderedDiagramsHaveSvg();
  }

  if (isReady()) {
    return;
  }

  await new Promise<void>((resolve, reject) => {
    const observer = new MutationObserver(() => {
      try {
        if (isReady()) {
          observer.disconnect();
          resolve();
        }
      } catch (error) {
        observer.disconnect();
        reject(error);
      }
    });

    try {
      if (isReady()) {
        resolve();
        return;
      }
      observer.observe(node, { childList: true, subtree: true });
    } catch (error) {
      observer.disconnect();
      reject(error);
    }
  });
}

export async function exportShareImageNode(node: HTMLElement): Promise<string> {
  await document.fonts?.ready;
  const restoreImages = await prepareImagesForExport(node);
  let restoreMermaidDiagrams = (): void => {};
  try {
    await waitForMermaidDiagrams(node);
    await nextFrame();

    // Scale down wide Mermaid diagrams so they are not clipped in the exported
    // image. toPng reads the DOM synchronously, so the restore callback must be
    // called after the render completes.
    restoreMermaidDiagrams = fitMermaidDiagramsForExport(node);
    await nextFrame();

    const backgroundColor = window.getComputedStyle(node).backgroundColor;
    return await toPng(node, {
      cacheBust: true,
      pixelRatio: SHARE_IMAGE_PIXEL_RATIO,
      width: SHARE_IMAGE_WIDTH,
      height: node.scrollHeight,
      backgroundColor,
    });
  } finally {
    restoreMermaidDiagrams();
    restoreImages();
  }
}
