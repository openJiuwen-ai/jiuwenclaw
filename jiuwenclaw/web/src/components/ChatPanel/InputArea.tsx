import { useState, useRef, useCallback, KeyboardEvent, PointerEvent as ReactPointerEvent, useEffect, CSSProperties } from 'react';
import { useTranslation } from 'react-i18next';
import { useSpeechRecognition } from '../../hooks';
import { stopAllTts } from '../../utils';
import { useChatStore, useSessionStore } from '../../stores';
import { AgentMode } from '../../types';
import clsx from 'clsx';

interface InputAreaProps {
  onSubmit: (content: string) => void;
  onInterrupt: (newInput?: string) => void;
  onSwitchMode: (mode: AgentMode) => void;
  isProcessing: boolean;
  onNewSession: () => void;
}

export function InputArea({
  onSubmit,
  onInterrupt,
  onSwitchMode,
  isProcessing,
  onNewSession,
}: InputAreaProps) {
  const [value, setValue] = useState('');
  const [pendingVoiceText, setPendingVoiceText] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const autoSendTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const isComposingRef = useRef(false);
  const activePointerIdRef = useRef<number | null>(null);
  const isVoicePressingRef = useRef(false);
  const { t } = useTranslation();
  const { isPaused, taskQueue, addToTaskQueue, removeFromTaskQueue } = useChatStore();
  const { mode } = useSessionStore();
  const isInterruptible = isProcessing || isPaused;
  const isAgentMode = mode === 'agent';
  const modes: Array<{ value: AgentMode; label: string; icon: JSX.Element }> = [
    { value: 'plan', label: t('chat.modePlan'), icon: (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z" />
      </svg>
    )},
    { value: 'agent', label: t('chat.modeAgent'), icon: (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z" />
      </svg>
    )},
  ];

  const {
    isListening,
    interimTranscript,
    startListening,
    stopListening,
    isSupported: speechSupported,
  } = useSpeechRecognition({
    language: 'cmn-Hans-CN',
    continuous: true,
    interimResults: true,
    silenceTimeoutMs: 8000,
    restartWhen: () => isVoicePressingRef.current,
    onResult: (text, isFinal) => {
      if (isFinal) {
        setPendingVoiceText((prev) => prev + text);
      }
    },
    onEnd: () => {
      autoSendTimeoutRef.current = setTimeout(() => {}, 100);
    },
    onError: (error) => {
      console.error('语音识别错误:', error);
    },
  });

  useEffect(() => {
    if (!isListening && pendingVoiceText) {
      const finalText = (value + pendingVoiceText).trim();
      if (finalText) {
        setValue(finalText);
        setPendingVoiceText('');

        setTimeout(() => {
          if (isInterruptible) {
            onInterrupt(finalText);
          } else {
            onSubmit(finalText);
          }
          setValue('');
          if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
          }
        }, 150);
      }
    }
  }, [isListening, pendingVoiceText, value, isInterruptible, onSubmit, onInterrupt]);

  useEffect(() => {
    return () => {
      if (autoSendTimeoutRef.current) {
        clearTimeout(autoSendTimeoutRef.current);
      }
    };
  }, []);

  const handleSubmit = useCallback(() => {
    const trimmed = (value + pendingVoiceText).trim();
    if (!trimmed) return;

    if (isListening) {
      stopListening();
    }

    if (isInterruptible) {
      if (isAgentMode) {
        // 智能执行模式下，将任务添加到队列
        addToTaskQueue(trimmed);
      } else {
        // 其他模式下，中断当前任务
        onInterrupt(trimmed);
      }
    } else {
      onSubmit(trimmed);
    }
    setValue('');
    setPendingVoiceText('');

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [value, pendingVoiceText, isInterruptible, isListening, onSubmit, onInterrupt, stopListening, isAgentMode, addToTaskQueue]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key !== 'Enter' || e.shiftKey) return;
      if (isComposingRef.current || e.nativeEvent.isComposing) return;
      e.preventDefault();
      handleSubmit();
    },
    [handleSubmit]
  );

  const handleInput = useCallback(() => {
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 200)}px`;
    }
  }, []);

  const handleVoiceStart = useCallback(() => {
    if (isListening) return;
    stopAllTts();
    startListening();
  }, [isListening, startListening]);

  const handleVoiceEnd = useCallback(() => {
    if (!isListening) return;
    stopListening();
  }, [isListening, stopListening]);

  const handleVoicePointerDown = useCallback(
    (e: ReactPointerEvent<HTMLButtonElement>) => {
      // 仅响应主按钮按压，避免右键/多指导致状态抖动
      if (e.pointerType === 'mouse' && e.button !== 0) return;
      if (activePointerIdRef.current !== null) return;
      e.preventDefault();
      activePointerIdRef.current = e.pointerId;
      isVoicePressingRef.current = true;
      e.currentTarget.setPointerCapture(e.pointerId);
      handleVoiceStart();
    },
    [handleVoiceStart]
  );

  const handleVoicePointerUp = useCallback(
    (e: ReactPointerEvent<HTMLButtonElement>) => {
      if (activePointerIdRef.current !== e.pointerId) return;
      e.preventDefault();
      activePointerIdRef.current = null;
      isVoicePressingRef.current = false;
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
      handleVoiceEnd();
    },
    [handleVoiceEnd]
  );

  const handleVoicePointerCancel = useCallback(
    (e: ReactPointerEvent<HTMLButtonElement>) => {
      if (activePointerIdRef.current !== e.pointerId) return;
      activePointerIdRef.current = null;
      isVoicePressingRef.current = false;
      if (e.currentTarget.hasPointerCapture(e.pointerId)) {
        e.currentTarget.releasePointerCapture(e.pointerId);
      }
      handleVoiceEnd();
    },
    [handleVoiceEnd]
  );

  const handleNewSession = useCallback(() => {
    if (isListening || isInterruptible) return;
    setValue('');
    setPendingVoiceText('');
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
    onNewSession();
  }, [isListening, isInterruptible, onNewSession]);

  const displayValue = isListening
    ? value + pendingVoiceText + interimTranscript
    : value + pendingVoiceText;

  const canSend = value.trim().length > 0 || isListening;
  const modeIndex = Math.max(0, modes.findIndex((m) => m.value === mode));

  return (
    <div
      className={cx(
        'chat-input-container',
        isListening && 'chat-input-container--recording',
      )}
    >
      {isListening && (
        <div className="chat-input-recording-bar">
          <span className="chat-input-recording-dot" />
          <span>{t('chat.recording')}</span>
        </div>
      )}

      {/* 智能执行模式下的等待任务盒子 */}
      {isAgentMode && taskQueue.length > 0 && (
        <div className="chat-input-task-queue">
          <div className="chat-input-task-queue-header">
            <svg className="w-4 h-4 mr-2" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-3 7h3m-3 4h3m-6-4h.01M9 16h.01" />
            </svg>
            {t('chat.waitingTasksCount', { count: taskQueue.length })}
          </div>
          <div className="chat-input-task-queue-list">
            {taskQueue.map((task) => (
              <div key={task.id} className="chat-input-task-item">
                <span className="chat-input-task-content">{task.content}</span>
                <button
                  type="button"
                  onClick={() => removeFromTaskQueue(task.id)}
                  className="chat-input-task-remove"
                  title={t('chat.removeTask')}
                >
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      <textarea
        ref={textareaRef}
        value={displayValue}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => { isComposingRef.current = true; }}
        onCompositionEnd={() => { isComposingRef.current = false; }}
        onInput={handleInput}
        placeholder={
          isListening
            ? t('chat.placeholderVoice')
            : isAgentMode && isInterruptible
            ? t('chat.placeholderProcessingQueue')
            : isInterruptible
            ? t('chat.placeholderProcessing')
            : t('chat.placeholder')
        }
        className="chat-input-textarea"
        rows={1}
      />

      <div className="chat-input-toolbar">
        <div className="chat-input-toolbar-left">
          <div
            className="chat-mode-switch"
            style={{ '--chat-mode-index': modeIndex } as CSSProperties}
          >
            <div className="chat-mode-switch__indicator" />
            {modes.map((m) => (
              <button
                type="button"
                key={m.value}
                onClick={() => {
                  if (mode !== m.value) {
                    onSwitchMode(m.value);
                  }
                }}
                className={clsx(
                  'chat-mode-btn',
                  mode === m.value ? 'chat-mode-btn--active' : 'chat-mode-btn--inactive'
                )}
              >
                {m.icon}
                {m.label}
              </button>
            ))}
          </div>

        </div>

        <div className="chat-input-actions">
          <button
            type="button"
            onClick={handleNewSession}
            disabled={isListening || isInterruptible}
            className={cx(
              'chat-input-btn',
              (isListening || isInterruptible) && 'chat-input-btn--disabled',
            )}
            title={isListening || isInterruptible ? t('chat.newSessionDisabled') : t('chat.newSession')}
          >
            <svg className="chat-input-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
            </svg>
          </button>

          {speechSupported && (
            <button
              type="button"
              onPointerDown={handleVoicePointerDown}
              onPointerUp={handleVoicePointerUp}
              onPointerCancel={handleVoicePointerCancel}
              className={cx(
                'chat-input-btn',
                isListening && 'chat-input-btn--recording',
              )}
              title={t('chat.holdToSpeak')}
            >
              {isListening ? (
                <svg className="chat-input-btn-icon" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="6" y="6" width="12" height="12" rx="2" />
                </svg>
              ) : (
                <svg className="chat-input-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 18.75a6 6 0 006-6v-1.5m-6 7.5a6 6 0 01-6-6v-1.5m6 7.5v3.75m-3.75 0h7.5M12 15.75a3 3 0 01-3-3V4.5a3 3 0 116 0v8.25a3 3 0 01-3 3z" />
                </svg>
              )}
            </button>
          )}

          <button
            type="button"
            onClick={handleSubmit}
            disabled={!canSend}
            className={cx(
              'chat-input-btn chat-input-btn--send',
              canSend ? 'chat-input-btn--send-active' : 'chat-input-btn--disabled',
            )}
            title={t('chat.send')}
          >
            <svg className="chat-input-btn-icon" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 10.5L12 3m0 0l7.5 7.5M12 3v18" />
            </svg>
          </button>
        </div>
      </div>
    </div>
  );
}

function cx(...classes: (string | boolean | undefined | null)[]) {
  return classes.filter(Boolean).join(' ');
}
