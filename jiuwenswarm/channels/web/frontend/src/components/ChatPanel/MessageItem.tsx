/**
 * MessageItem 组件
 *
 * 单条消息显示，支持 TTS 朗读
 */

import { useState, useCallback, useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Message, FileDownloadItem } from '../../types';
import { StreamingContent } from './StreamingContent';
import { ToolCallDisplay } from './ToolCallDisplay';
import { MediaRenderer } from './MediaRenderer';
import { formatTimestamp, onTtsStop, sanitizeTtsText } from '../../utils';
import { useSpeechSynthesis } from '../../hooks';
import clsx from 'clsx';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { parseTeamEventMessage } from './teamEventUtils';
import teamLeaderAvatar from '../../assets/teamleader.svg';
import userInTeamAvatar from '../../assets/user-in-team.svg';
import teamAvatar2 from '../../assets/Team-2.svg';
import teamAvatar3 from '../../assets/Team-3.svg';
import teamAvatar4 from '../../assets/Team-4.svg';
import teamAvatar5 from '../../assets/Team-5.svg';
import teamAvatar6 from '../../assets/Team-6.svg';

const TEAM_MEMBER_AVATARS = [
  teamAvatar2,
  teamAvatar3,
  teamAvatar4,
  teamAvatar5,
  teamAvatar6,
];

function normalizeMemberId(member?: string) {
  return member?.trim().toLowerCase().replace(/[\s-]+/g, '_') ?? '';
}

function isTeamLeaderMember(member?: string) {
  const normalized = normalizeMemberId(member);
  return normalized === 'team_leader' || normalized === 'teamleader';
}

function isUserMember(member?: string) {
  return normalizeMemberId(member) === 'user';
}

function hashMemberKey(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1) {
    hash = (hash * 33 + value.charCodeAt(index)) >>> 0;
  }
  return hash;
}

function getTeamMemberAvatar(member?: string) {
  if (isTeamLeaderMember(member)) {
    return {
      src: teamLeaderAvatar,
      containerClassName: 'bg-transparent',
      imageClassName: 'h-full w-full rounded-2xl object-cover',
    };
  }

  if (isUserMember(member)) {
    return {
      src: userInTeamAvatar,
      containerClassName: 'bg-transparent',
      imageClassName: 'h-full w-full rounded-xl object-cover',
    };
  }

  const normalized = normalizeMemberId(member) || 'unknown_member';
  const src = TEAM_MEMBER_AVATARS[
    hashMemberKey(normalized) % TEAM_MEMBER_AVATARS.length
  ];

  return {
    src,
    containerClassName: 'bg-transparent',
    imageClassName: 'h-full w-full rounded-2xl object-cover',
  };
}

export function TeamMemberAvatar({ member }: { member?: string }) {
  const avatar = getTeamMemberAvatar(member);

  return (
    <div className={clsx(
      'h-9 w-9 shrink-0 overflow-hidden rounded-xl',
      avatar.containerClassName
    )}>
      <img
        src={avatar.src}
        alt={`${formatMemberName(member)} avatar`}
        className={avatar.imageClassName}
      />
    </div>
  );
}

export function formatMemberName(member?: string) {
  if (isTeamLeaderMember(member)) {
    return 'TeamLeader';
  }

  if (isUserMember(member)) {
    return 'User';
  }

  return member || 'Unknown';
}

export function MarkdownMessageBody({
  content,
  className,
  testId,
}: {
  content: string;
  className?: string;
  testId?: string;
}) {
  return (
    <div className={clsx('chat-text chat-markdown', className)} data-testid={testId}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ href, children, ...props }) => (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              {...props}
            >
              {children}
            </a>
          ),
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}

export function TeamMemberMessageFrame({
  member,
  showAvatar = true,
  children,
  contentClassName,
}: {
  member?: string;
  showAvatar?: boolean;
  children: ReactNode;
  contentClassName?: string;
}) {
  return (
    <div className="team-member-message animate-fade-in">
      {showAvatar && (
        <div className="team-member-message__header">
          <TeamMemberAvatar member={member} />
        </div>
      )}
      <div className={clsx('team-member-message__body', !showAvatar && 'is-continued', contentClassName)}>
        {children}
      </div>
    </div>
  );
}

function TeamLeaderPlainTextMessage({
  member = 'team_leader',
  content,
  showAvatar = true,
  fileItems,
}: {
  member?: string;
  content: string;
  showAvatar?: boolean;
  fileItems?: FileDownloadItem[];
}) {
  return (
    <TeamMemberMessageFrame
      member={member}
      showAvatar={showAvatar}
    >
      {fileItems && fileItems.length > 0 && (
        <FileDownloadList files={fileItems} className="w-full md:w-1/2" />
      )}
      <MarkdownMessageBody
        content={content}
        className="team-member-message__plain"
      />
    </TeamMemberMessageFrame>
  );
}

export function getMessageActor(message: Message): string | null {
  if (message.role !== 'system') {
    return null;
  }

  if (message.content?.startsWith('team.event:')) {
    const event = parseTeamEventMessage(message);
    return event?.fromMember || null;
  }

  if (message.id?.startsWith('team-leader-')) {
    return 'team_leader';
  }

  return null;
}

interface MessageItemProps {
  message: Message;
  autoSpeak?: boolean;
  showAvatar?: boolean;
}

export function MessageItem({
  message,
  autoSpeak = false,
  showAvatar = true,
}: MessageItemProps) {
  const { t } = useTranslation();
  const {
    id,
    role,
    content,
    timestamp,
    isStreaming,
    toolCall,
    toolResult,
    audioBase64,
    audioMime,
    mediaItems,
    fileItems,
  } = message;
  const [hasAutoSpoken, setHasAutoSpoken] = useState(false);
  const [isAudioPlaying, setIsAudioPlaying] = useState(false);
  const [isExpanded, setIsExpanded] = useState(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  // TTS
  const { isSpeaking, speak, stop, isSupported: ttsSupported } = useSpeechSynthesis({
    language: 'zh-CN',
    rate: 1.1,
  });

  // 朗读消息
  const stopGeneratedAudio = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current.currentTime = 0;
      audioRef.current = null;
    }
    setIsAudioPlaying(false);
  }, []);

  const playGeneratedAudio = useCallback(async () => {
    if (!audioBase64) {
      return false;
    }

    stopGeneratedAudio();
    const audio = new Audio(
      `data:${audioMime || 'audio/mpeg'};base64,${audioBase64}`
    );
    audioRef.current = audio;
    audio.onended = () => {
      setIsAudioPlaying(false);
    };
    audio.onerror = () => {
      setIsAudioPlaying(false);
    };

    try {
      await audio.play();
      setIsAudioPlaying(true);
      return true;
    } catch {
      setIsAudioPlaying(false);
      return false;
    }
  }, [audioBase64, audioMime, stopGeneratedAudio]);

  const handleSpeak = useCallback(() => {
    if (audioBase64) {
      if (isAudioPlaying) {
        stopGeneratedAudio();
        return;
      }
      void playGeneratedAudio();
      return;
    }

    if (isSpeaking) {
      stop();
    } else if (content) {
      const cleanContent = sanitizeTtsText(content);
      if (cleanContent) {
        speak(cleanContent);
      }
    }
  }, [
    audioBase64,
    content,
    isAudioPlaying,
    isSpeaking,
    playGeneratedAudio,
    speak,
    stop,
    stopGeneratedAudio,
  ]);

  const handleCopy = useCallback(async () => {
    if (!content) return;
    try {
      await navigator.clipboard.writeText(content);
    } catch {
      const textarea = document.createElement('textarea');
      textarea.value = content;
      textarea.style.position = 'fixed';
      textarea.style.opacity = '0';
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand('copy');
      document.body.removeChild(textarea);
    }
  }, [content]);

  // 自动朗读新消息（仅助手消息，由父组件通过 autoSpeak 控制）
  useEffect(() => {
    if (autoSpeak && role === 'assistant' && !isStreaming && !hasAutoSpoken && content) {
      handleSpeak();
      setHasAutoSpoken(true);
    }
  }, [autoSpeak, role, isStreaming, hasAutoSpoken, content, handleSpeak]);

  useEffect(() => {
    return () => {
      stopGeneratedAudio();
    };
  }, [stopGeneratedAudio]);

  useEffect(() => {
    return onTtsStop(() => {
      stopGeneratedAudio();
      stop();
    });
  }, [stopGeneratedAudio, stop]);

  // 工具调用/结果消息
  if (role === 'tool') {
    return (
      <ToolCallDisplay
        toolCall={toolCall}
        toolResult={toolResult}
      />
    );
  }

  // 系统消息
  if (role === 'system') {
 	     // 检查是否为 chat.session_result 事件
 	     if (content && content.startsWith('chat.session_result:')) {
 	       console.log('chat.session_result event:', content);
 	       const [, jsonStr] = content.split('chat.session_result:');
 	       try {
 	         const sessionData = JSON.parse(jsonStr);
 	         console.log('Parsed session data:', sessionData);
 	         const { description, result } = sessionData;
 	         
 	         return (
 	           <div className="chat-tool-card animate-rise">
 	             <div
 	               className="cursor-pointer"
 	               onClick={() => setIsExpanded(!isExpanded)}
 	             >
 	               <div className="flex items-center gap-2">
 	                 <span className="w-5 h-5 rounded bg-accent-2-subtle text-accent-2 flex items-center justify-center text-sm">
 	                   <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
 	                     <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V19.5a2.25 2.25 0 002.25 2.25h.75m0-3h-3.75m0 0h-3.75m0 0H9m1.5 3h3.75m-3.75 0H9m1.5 3h3.75m-3.75 0H9m1.5 3h3.75m-3.75 0H9" />
 	                   </svg>
 	                 </span>
 	                 <span className="font-mono text-sm font-medium text-text">
 	                   会话任务：【{description || '未知任务'}】已完成
 	                 </span>
 	                 <span className="text-text-muted text-sm">
 	                   {isExpanded ? '▼' : '▶'}
 	                 </span>
 	               </div>
 	             </div>
 	             {isExpanded && (
 	               <div className="mt-2 p-2 rounded-md bg-card border border-border">
 	                 {description && (
 	                   <div className="mb-2">
 	                     <div className="font-mono text-xs text-text-muted mb-1">Description:</div>
 	                     <pre className="font-mono text-sm text-text overflow-x-auto whitespace-pre-wrap">
 	                       {description}
 	                     </pre>
 	                   </div>
 	                 )}
 	                 {result && (
 	                   <div>
 	                     <div className="font-mono text-xs text-text-muted mb-1">Result:</div>
 	                     <pre className="font-mono text-sm text-text overflow-x-auto whitespace-pre-wrap max-h-60">
 	                       {result}
 	                     </pre>
 	                   </div>
 	                 )}
 	               </div>
 	             )}
 	           </div>
 	         );
 	       } catch (e) {
 	         // 如果解析失败，显示原始内容
 	         return (
 	           <div className="flex justify-center my-4 animate-fade-in">
 	             <div className="px-4 py-2 rounded-full bg-secondary border border-border text-text-muted text-sm">
 	               {content}
 	             </div>
 	           </div>
 	         );
 	       }
 	     }
	     
	     // 检查是否为团队消息
	     if (content && content.startsWith('team.event:')) {
	       const event = parseTeamEventMessage(message);
	       if (event) {
	           // team_leader 发给用户的消息（不是 p2p 也不是 broadcast）
	           if (event.isLeaderToUser) {
	             return (
	               <TeamLeaderPlainTextMessage
	                 member={event.fromMember}
	                 content={event.content}
	                 showAvatar={showAvatar}
	               />
	             );
	           }
	           
	           // p2p 和 broadcast 消息展示
	           return (
	             <TeamMemberMessageFrame
	               member={event.fromMember}
	               showAvatar={showAvatar}
	             >
	               <div className="team-member-message__card">
	                 <div className="team-member-message__content">
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
	             </TeamMemberMessageFrame>
	           );
	       }
	       return (
	         <div className="flex justify-center my-4 animate-fade-in">
	           <div className="px-4 py-2 rounded-full bg-secondary border border-border text-text-muted text-sm">
	             {content}
	           </div>
	         </div>
	       );
	     }
	     
	     // 检查是否为 team_leader 消息（通过 ID 判断）
	     const isTeamLeaderMsg = id && id.startsWith('team-leader-');
	     
	     if (isTeamLeaderMsg) {
	       let messageContent = content;
	       
	       if (content.startsWith('team.leader:')) {
	         const [, jsonStr] = content.split('team.leader:');
	         try {
	           const data = JSON.parse(jsonStr);
	           messageContent = data.content;
	         } catch (e) {
	         }
	       }
	       
	       return (
	         <TeamLeaderPlainTextMessage
	           member="team_leader"
	           content={messageContent || (isStreaming ? '正在接收中...' : '')}
	           showAvatar={showAvatar}
	           fileItems={fileItems}
	         />
	       );
	     }
	     
    return (
      <div className="flex justify-center my-4 animate-fade-in">
        <div className="px-4 py-2 rounded-full bg-secondary border border-border text-text-muted text-sm">
          {content}
        </div>
      </div>
    );
  }

  // 用户/助手消息
  const isUser = role === 'user';
  const showTTS = Boolean(
    !isUser && !isStreaming && content && (ttsSupported || audioBase64)
  );
  const showCopy = Boolean(content) && !isStreaming;
  const isPlaying = audioBase64 ? isAudioPlaying : isSpeaking;

  return (
    <div className={clsx(
      'flex mb-3 animate-rise',
      isUser ? 'justify-end' : 'justify-start'
    )}>
      <div className="max-w-[82%] min-w-0">
        {!isUser && (
          <div className="hidden" data-testid="thinking-summary" aria-hidden="true" />
        )}

        <div
          className={clsx(
            'chat-bubble relative group',
            isUser ? 'user' : 'assistant',
            !isUser && !isStreaming && 'markdown',
            isStreaming && 'streaming'
          )}
          data-testid={!isUser ? 'thinking-panel' : undefined}
        >
          {isStreaming ? (
            <StreamingContent content={content} isStreaming={true} />
          ) : (
            <>
              {isUser ? (
                <div className="chat-text">
                  <span className="whitespace-pre-wrap">{content}</span>
                </div>
              ) : (
                <MarkdownMessageBody
                  content={content}
                  testId="thinking-body"
                />
              )}
              {mediaItems && mediaItems.length > 0 && (
                <MediaRenderer items={mediaItems} />
              )}
              {fileItems && fileItems.length > 0 && (
                <FileDownloadList files={fileItems} />
              )}
            </>
          )}
        </div>

        {/* Token usage summary */}
        {!isUser && !isStreaming && message.usageSummary && message.usageSummary.total_tokens > 0 && (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-text-muted mt-1 mb-0.5">
            <span>
              {message.usageSummary.input_tokens.toLocaleString()} in /{' '}
              {message.usageSummary.output_tokens.toLocaleString()} out /{' '}
              {message.usageSummary.total_tokens.toLocaleString()} total
            </span>
            {message.usageSummary.total_cost != null && message.usageSummary.total_cost > 0 && (
              <span>
                ${message.usageSummary.input_cost?.toFixed(4)} in /{' '}
                ${message.usageSummary.output_cost?.toFixed(4)} out /{' '}
                ${message.usageSummary.total_cost.toFixed(4)} total
              </span>
            )}
          </div>
        )}

        {!isStreaming && (
          <div
            className={clsx(
              'flex items-center gap-3 text-sm mt-2 text-text-muted',
              isUser ? 'justify-end' : 'justify-start'
            )}
          >
            <span>{formatTimestamp(timestamp)}</span>
            
            {showCopy && (
              <button
                onClick={handleCopy}
                className="p-1.5 rounded-md transition-colors hover:text-accent hover:bg-secondary"
                title={t('chatUi.copyMessage')}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 6.75h7.5m-7.5 3h7.5m-7.5 3h4.5M6.75 3h7.5A2.25 2.25 0 0116.5 5.25v13.5A2.25 2.25 0 0114.25 21h-7.5A2.25 2.25 0 014.5 18.75V5.25A2.25 2.25 0 016.75 3z" />
                </svg>
              </button>
            )}

            {showTTS && (
              <button
                onClick={handleSpeak}
                className={clsx(
                  'p-1.5 rounded-md transition-colors',
                  isPlaying
                    ? 'text-accent bg-accent/10'
                    : 'hover:text-accent hover:bg-secondary'
                )}
                title={isPlaying ? t('chatUi.stopReading') : t('chatUi.readMessage')}
              >
                {isPlaying ? (
                  <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 24 24">
                    <rect x="6" y="6" width="12" height="12" rx="2" />
                  </svg>
                ) : (
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 010 12.728M16.463 8.288a5.25 5.25 0 010 7.424M6.75 8.25l4.72-4.72a.75.75 0 011.28.53v15.88a.75.75 0 01-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.01 9.01 0 012.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75z" />
                  </svg>
                )}
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

function formatFileSize(bytes: number | undefined): string {
  if (bytes === undefined || bytes === null || isNaN(bytes)) return '';
  if (bytes === 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const size = bytes / Math.pow(1024, i);
  return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function getFileExtension(name: string): string {
  const parts = name.split('.');
  if (parts.length < 2) return '';
  return parts[parts.length - 1].toUpperCase();
}

function getFileTypeConfig(mimeType: string | undefined, name: string) {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  const mt = mimeType || '';
  if (mt.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'svg', 'webp', 'bmp'].includes(ext))
    return { label: 'IMG', bg: 'bg-[#3370ff]', icon: '🖼' };
  if (mt.startsWith('audio/') || ['mp3', 'wav', 'aac', 'flac', 'ogg'].includes(ext))
    return { label: 'AUDIO', bg: 'bg-[#7b67ee]', icon: '🎵' };
  if (mt.startsWith('video/') || ['mp4', 'avi', 'mov', 'mkv', 'webm'].includes(ext))
    return { label: 'VIDEO', bg: 'bg-[#f77234]', icon: '🎬' };
  if (mt.includes('pdf') || ext === 'pdf')
    return { label: 'PDF', bg: 'bg-[#f54a45]', icon: '📄' };
  if (mt.includes('presentation') || mt.includes('ppt') || ['ppt', 'pptx'].includes(ext))
    return { label: 'PPT', bg: '#FFFFFF', icon: (
      <svg viewBox="0 0 1024 1024" className="w-7 h-7">
        <path d="M145.6 0C100.8 0 64 36.8 64 81.6v860.8C64 987.2 100.8 1024 145.6 1024h732.8c44.8 0 81.6-36.8 81.6-81.6V324.8L657.6 0h-512z" fill="#E34221" />
        <path d="M960 326.4v16H755.2s-100.8-20.8-99.2-108.8c0 0 4.8 92.8 97.6 92.8H960z" fill="#DC3119" />
        <path d="M657.6 0v233.6c0 25.6 17.6 92.8 97.6 92.8H960L657.6 0z" fill="#FFFFFF" opacity=".5" />
        <path d="M304 784h-54.4v67.2c0 6.4-4.8 11.2-11.2 11.2-6.4 0-12.8-4.8-12.8-11.2V686.4c0-9.6 8-17.6 17.6-17.6H304c38.4 0 59.2 25.6 59.2 57.6S340.8 784 304 784z m-3.2-94.4h-51.2v73.6h51.2c22.4 0 38.4-16 38.4-36.8 0-22.4-16-36.8-38.4-36.8zM480 784h-54.4v67.2c0 6.4-4.8 11.2-11.2 11.2-6.4 0-11.2-4.8-11.2-11.2V686.4c0-9.6 6.4-17.6 16-17.6H480c38.4 0 59.2 25.6 59.2 57.6S518.4 784 480 784z m-3.2-94.4h-49.6v73.6h49.6c22.4 0 38.4-16 38.4-36.8 0-22.4-16-36.8-38.4-36.8z m225.6 0h-52.8v161.6c0 6.4-4.8 11.2-11.2 11.2-6.4 0-12.8-4.8-12.8-11.2V689.6h-51.2c-6.4 0-11.2-4.8-11.2-11.2 0-4.8 4.8-9.6 11.2-9.6h128c6.4 0 11.2 4.8 11.2 11.2 0 4.8-4.8 9.6-11.2 9.6z" fill="#FFFFFF" />
      </svg>
    ) };
  if (mt.includes('spreadsheet') || mt.includes('excel') || mt.includes('xlsx') || ['xls', 'xlsx', 'csv'].includes(ext))
    return { label: 'XLS', bg: 'bg-[#2b9348]', icon: '📗' };
  if (mt.includes('word') || mt.includes('document') || mt.includes('docx') || ['doc', 'docx'].includes(ext))
    return { label: 'DOC', bg: 'bg-[#3370ff]', icon: '📝' };
  if (mt.includes('zip') || mt.includes('compressed') || mt.includes('archive') || ['zip', 'rar', '7z', 'tar', 'gz'].includes(ext))
    return { label: 'ZIP', bg: 'bg-[#8b5cf6]', icon: '📦' };
  if (['txt', 'md', 'log'].includes(ext))
    return { label: 'TXT', bg: 'bg-[#6b7280]', icon: '📃' };
  if (['json', 'xml', 'yaml', 'yml', 'toml', 'ini', 'cfg'].includes(ext))
    return { label: 'CFG', bg: 'bg-[#6b7280]', icon: '⚙' };
  if (['py', 'js', 'ts', 'java', 'go', 'rs', 'cpp', 'c', 'h'].includes(ext))
    return { label: 'CODE', bg: 'bg-[#6b7280]', icon: '�' };
  return { label: 'FILE', bg: 'bg-[#6b7280]', icon: '��' };
}

function FileDownloadList({
  files,
  className,
}: {
  files: FileDownloadItem[];
  className?: string;
}) {
  const handleDownload = async (file: FileDownloadItem) => {
    // 检查是否在 PyWebView 环境中（exe 模式）
    const pywebviewApi = (window as Window & { pywebview?: { api?: { download_file?: (url: string, filename: string) => Promise<boolean> | boolean } } }).pywebview?.api;
    if (pywebviewApi?.download_file) {
      // exe 模式：通过 webview API 下载
      const success = await pywebviewApi.download_file(file.download_url, file.name || 'download');
      if (!success) {
        console.error('Download failed via pywebview API');
      }
      return;
    }
    // 浏览器模式：使用标准 <a> 标签下载
    const link = document.createElement('a');
    link.href = file.download_url;
    link.download = file.name || '';
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div className={clsx('mt-2 space-y-2', className)}>
      {files.map((file, index) => {
        const typeConfig = getFileTypeConfig(file.mime_type, file.name);
        const ext = getFileExtension(file.name);
        return (
          <div
            key={`${file.name}-${index}`}
            className="flex items-center gap-3 rounded-lg border border-border bg-card px-3 py-2.5 hover:shadow-md hover:border-border-hover transition-all duration-fast cursor-pointer group"
            onClick={() => handleDownload(file)}
          >
            <div className={`flex-shrink-0 w-10 h-10 rounded-lg ${typeConfig.bg} flex items-center justify-center`}>
              {typeof typeConfig.icon === 'string' ? (
                <span className="text-white text-base leading-none select-none">{typeConfig.icon}</span>
              ) : (
                typeConfig.icon
              )}
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-text leading-snug truncate">{file.name}</div>
              <div className="flex items-center gap-1.5 mt-0.5">
                <span className="inline-flex items-center px-1 py-px rounded text-[10px] font-mono font-medium text-text-muted bg-secondary leading-none">
                  {ext || typeConfig.label}
                </span>
                <span className="text-xs text-text-muted">{formatFileSize(file.size)}</span>
              </div>
            </div>
            <div
              className="flex-shrink-0 w-8 h-8 rounded-lg flex items-center justify-center text-text-muted group-hover:text-accent group-hover:bg-accent-subtle transition-colors duration-fast"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 005.25 21h13.5A2.25 2.25 0 0021 18.75V16.5M16.5 12L12 16.5m0 0L7.5 12m4.5 4.5V3" />
              </svg>
            </div>
          </div>
        );
      })}
    </div>
  );
}
