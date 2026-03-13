/**
 * ChatPanel 组件
 *
 * 聊天面板，包含消息列表和输入区域
 */

import { useRef, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useChatStore } from '../../stores';
import { AgentMode, UserAnswer } from '../../types';
import { MessageList } from './MessageList';
import { InputArea } from './InputArea';
import { SubtaskProgress } from './SubtaskProgress';
import { InlineQuestionCard } from './InlineQuestionCard';
import './ChatPanel.css';

interface ChatPanelProps {
  onSendMessage: (content: string) => void;
  onInterrupt: (newInput?: string) => void;
  onSwitchMode: (mode: AgentMode) => void;
  isProcessing: boolean;
  onNewSession: () => void;
  onUserAnswer: (requestId: string, answers: UserAnswer[]) => void;
}

function ThinkingIndicator() {
  return (
    <div className="flex justify-start animate-rise">
      <div className="chat-bubble assistant chat-reading-indicator">
        <div className="chat-reading-indicator__dots">
          <span />
          <span />
          <span />
        </div>
      </div>
    </div>
  );
}


function SuggestionCard({ text, onClick }: { text: string; onClick: () => void }) {
  return (
    <button className="chat-suggestion-card" onClick={onClick}>
      <svg className="chat-suggestion-card__icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
      </svg>
      <span className="chat-suggestion-card__text">{text}</span>
      <svg className="chat-suggestion-card__arrow" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
      </svg>
    </button>
  );
}


export function ChatPanel({
  onSendMessage,
  onInterrupt,
  onSwitchMode,
  isProcessing,
  onNewSession,
  onUserAnswer,
}: ChatPanelProps) {
  const { t } = useTranslation();
  const { messages, isThinking } = useChatStore();
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const suggestions = [
    t('chat.welcomeSuggestions.journey'),
    t('chat.welcomeSuggestions.skills'),
  ];

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, isThinking]);

  const handleSuggestion = useCallback(
    (text: string) => onSendMessage(text),
    [onSendMessage],
  );

  return (
    <div className="flex flex-col h-full">
      <div className="flex-1 overflow-y-auto px-3 py-4">
        {messages.length === 0 ? (
          <div className="chat-welcome">
            <img src="/logo.png" alt={t('chat.welcomeLogoAlt')} className="chat-welcome__logo" />
            <h2 className="chat-welcome__heading">{t('chat.welcomeHeading')}</h2>
            <p className="chat-welcome__subtext">
              {t('chat.welcomeSubtext')}
            </p>
            <div className="chat-suggestions">
              {suggestions.map((text) => (
                <SuggestionCard key={text} text={text} onClick={() => handleSuggestion(text)} />
              ))}
            </div>
          </div>
        ) : (
          <>
            <MessageList messages={messages} />
            <SubtaskProgress />
            {/* 内联演进审批卡片 */}
            <InlineQuestionCard onSubmit={onUserAnswer} />
            {/* 思考中指示器 */}
            {isThinking && <ThinkingIndicator />}
          </>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-compose px-3 pb-4">
        <InputArea
          onSubmit={onSendMessage}
          onInterrupt={onInterrupt}
          onSwitchMode={onSwitchMode}
          isProcessing={isProcessing}
          onNewSession={onNewSession}
        />
      </div>
    </div>
  );
}
