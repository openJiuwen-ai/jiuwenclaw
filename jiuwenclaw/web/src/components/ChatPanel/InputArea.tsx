import { useState, useRef, useCallback, KeyboardEvent, PointerEvent as ReactPointerEvent, useEffect, CSSProperties } from 'react';
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
  const { isPaused } = useChatStore();
  const { mode } = useSessionStore();
  const isInterruptible = isProcessing || isPaused;
  const modes: Array<{ value: AgentMode; label: string; icon: JSX.Element }> = [
    { value: 'plan', label: '任务规划', icon: (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z" />
      </svg>
    )},
    { value: 'agent', label: '智能执行', icon: (
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
      onInterrupt(trimmed);
    } else {
      onSubmit(trimmed);
    }
    setValue('');
    setPendingVoiceText('');

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [value, pendingVoiceText, isInterruptible, isListening, onSubmit, onInterrupt, stopListening]);

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
          <span>正在录音...（松开按钮自动发送）</span>
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
            ? '正在听取语音...'
            : isInterruptible
            ? '处理中，输入新指令可中断...'
            : 'Enter 发送，Shift+Enter 换行，按住语音键开始录音'
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
            title={isListening || isInterruptible ? '处理中或录音中不可新建' : '新建会话'}
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
              title="按住说话"
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
            title="发送 (Enter)"
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
