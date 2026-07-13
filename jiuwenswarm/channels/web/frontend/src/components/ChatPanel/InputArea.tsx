import { useState, useRef, useCallback, KeyboardEvent, useEffect, ClipboardEvent, DragEvent, ChangeEvent, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { CircleX, FileImage, Loader2, Plus, Square, X } from 'lucide-react';
import { useSpeechRecognition } from '../../hooks';

// import { stopAllTts } from '../../utils';
import { useChatStore, useSessionStore } from '../../stores';
import { AgentMode, MediaItem } from '../../types';
import clsx from 'clsx';
import { getEvolutionPillLabel } from './evolution-status';
import sendIcon from '../../assets/send.svg';
import sendActiveIcon from '../../assets/send_active.svg';

interface InputAreaProps {
  onSubmit: (content: string, mediaItems?: MediaItem[]) => void;
  onPersistMedia: (content: string, mediaItems: MediaItem[]) => Promise<PersistMediaResponse>;
  onInterrupt: (newInput?: string) => void;
  onCancel: () => void;
  onSwitchMode: (mode: AgentMode) => void;
  isProcessing: boolean;
  onNewSession: () => Promise<void>;
}

const ACCEPTED_IMAGE_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp', 'image/gif']);
const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
const MAX_IMAGE_COUNT = 20;

type AttachmentStatus = 'uploading' | 'ready' | 'error';

interface AttachmentDraft {
  id: string;
  filename: string;
  mimeType: string;
  size: number;
  status: AttachmentStatus;
  base64Data?: string;
  previewUrl?: string;
  persistedMediaItem?: Record<string, unknown>;
  error?: string;
  file?: File;
}

interface AttachmentAlert {
  id: string;
  message: string;
}

interface PersistMediaResponse {
  content?: string;
  query?: string;
  media_items?: Record<string, unknown>[];
  files?: Record<string, unknown>;
}

function formatAttachmentSize(size: number): string {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}

function makeAttachmentId(file: File): string {
  const random = typeof crypto !== 'undefined' && 'randomUUID' in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${file.name || 'image'}-${file.size}-${random}`;
}

function attachmentToMediaItem(attachment: AttachmentDraft): MediaItem {
  const persisted = attachment.persistedMediaItem;
  const filename = pickString(persisted?.filename) || attachment.filename;
  const mimeType = pickString(persisted?.mime_type, persisted?.mimeType) || attachment.mimeType;
  const sizeBytes = pickNumber(persisted?.size_bytes, persisted?.sizeBytes) ?? attachment.size;
  return {
    type: 'image',
    mimeType,
    mime_type: mimeType,
    filename,
    base64Data: attachment.base64Data,
    path: pickString(persisted?.path),
    sizeBytes,
    size_bytes: sizeBytes,
  };
}

function buildUploadMediaItem(attachment: AttachmentDraft, payload: Pick<AttachmentDraft, 'base64Data'>): MediaItem {
  return {
    type: 'image',
    mimeType: attachment.mimeType,
    filename: attachment.filename,
    base64Data: payload.base64Data,
  };
}

function pickString(...values: unknown[]): string | undefined {
  for (const value of values) {
    if (typeof value === 'string' && value.trim()) {
      return value;
    }
  }
  return undefined;
}

function pickNumber(...values: unknown[]): number | undefined {
  for (const value of values) {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return value;
    }
  }
  return undefined;
}

function getImageValidationError(file: File): string | null {
  if (!ACCEPTED_IMAGE_TYPES.has(file.type)) {
    return `文件类型不支持：${file.name || '未命名文件'}`;
  }
  if (file.size > MAX_IMAGE_BYTES) {
    return `文件大小超出限制：${file.name || '未命名文件'}（最大${formatAttachmentSize(MAX_IMAGE_BYTES)}）`;
  }
  return null;
}

function readImageFile(file: File): Promise<Pick<AttachmentDraft, 'base64Data' | 'previewUrl'> | null> {
  if (getImageValidationError(file)) {
    return Promise.resolve(null);
  }
  return new Promise((resolve) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : '';
      const base64Data = result.includes(',') ? result.split(',')[1] : '';
      if (!base64Data) {
        resolve(null);
        return;
      }
      resolve({ base64Data, previewUrl: result });
    };
    reader.onerror = () => resolve(null);
    reader.readAsDataURL(file);
  });
}

function ClusterIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path
        fillRule="nonzero"
        d="M13.794 3.53268L9.37399 0.986016C8.62732 0.559349 7.70732 0.559349 6.96065 0.986016L2.54065 3.53268C1.79398 3.95935 1.33398 4.75935 1.33398 5.61935L1.33398 10.7127C1.33398 11.5727 1.79398 12.3727 2.54065 12.7993L6.96065 15.346C7.33398 15.5593 7.74732 15.666 8.16732 15.666C8.58732 15.666 9.00065 15.5593 9.37399 15.346L13.794 12.7993C14.5407 12.3727 15.0007 11.5727 15.0007 10.7127L15.0007 5.61935C15.0007 4.75935 14.5407 3.95935 13.794 3.53268ZM14.0007 10.7127C14.0007 11.2127 13.7273 11.6793 13.294 11.9327L8.87399 14.4793C8.43398 14.7327 7.89398 14.7327 7.46065 14.4793L3.04065 11.9327C2.60732 11.6793 2.33398 11.2127 2.33398 10.7127L2.33398 5.61935C2.33398 5.11935 2.60732 4.65268 3.04065 4.39935L7.46065 1.85268C7.68065 1.72602 7.92065 1.66602 8.16732 1.66602C8.41398 1.66602 8.65398 1.72602 8.87399 1.85268L13.294 4.39935C13.7273 4.65268 14.0007 5.11935 14.0007 5.61935L14.0007 10.7127ZM11.8807 7.86602L10.4007 7.01268L10.4007 5.29935C10.4007 5.11935 10.3073 4.95268 10.1473 4.86602L8.41398 3.86602C8.26065 3.77935 8.06732 3.77935 7.91398 3.86602L6.18065 4.86602C6.02732 4.95268 5.92732 5.11935 5.92732 5.29935L5.92732 7.01268L4.44732 7.86602C4.29398 7.95268 4.19398 8.11935 4.19398 8.29935L4.19398 10.2993C4.19398 10.4793 4.28732 10.646 4.44732 10.7327L6.18065 11.7327C6.26065 11.7793 6.34732 11.7993 6.43398 11.7993C6.52065 11.7993 6.60732 11.7793 6.68732 11.7327L8.16732 10.8793L9.64732 11.7327C9.72732 11.7793 9.81398 11.7993 9.90065 11.7993C9.98732 11.7993 10.074 11.7793 10.154 11.7327L11.8873 10.7327C12.0407 10.646 12.1407 10.4793 12.1407 10.2993L12.1407 8.29935C12.1407 8.11935 12.0407 7.95268 11.8807 7.86602ZM6.93398 5.58602L8.16732 4.87268L9.40065 5.58602L9.40065 7.00602L8.16732 7.71935L6.93398 7.00602L6.93398 5.58602ZM6.43398 10.7193L5.20065 10.006L5.20065 8.58602L6.43398 7.87268L7.66732 8.58602L7.66732 10.006L6.43398 10.7193ZM11.1273 10.006L9.89398 10.7193L8.66065 10.006L8.66065 8.58602L9.89398 7.87268L11.1273 8.58602L11.1273 10.006Z"
      />
    </svg>
  );
}

export function InputArea({
  onSubmit,
  onPersistMedia,
  onInterrupt,
  onCancel,
  onSwitchMode,
  isProcessing,
  onNewSession,
}: InputAreaProps) {
  const [pendingVoiceText, setPendingVoiceText] = useState('');
  const [isModeMenuOpen, setIsModeMenuOpen] = useState(false);
  const [showModeSwitchModal, setShowModeSwitchModal] = useState(false);
  const [pendingMode, setPendingMode] = useState<AgentMode | null>(null);
  const [attachments, setAttachments] = useState<AttachmentDraft[]>([]);
  const [attachmentAlerts, setAttachmentAlerts] = useState<AttachmentAlert[]>([]);
  const [attachmentMenuId, setAttachmentMenuId] = useState<string | null>(null);
  const [isDraggingImage, setIsDraggingImage] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const modeMenuRef = useRef<HTMLDivElement>(null);
  const autoSendTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attachmentMenuTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const attachmentMenuOpenedByLongPressRef = useRef(false);
  const isComposingRef = useRef(false);
  // const activePointerIdRef = useRef<number | null>(null);
  const isVoicePressingRef = useRef(false);
  const { t } = useTranslation();
  const {
    isPaused,
    taskQueue,
    addToTaskQueue,
    removeFromTaskQueue,
    inputValue,
    setInputValue,
    messages,
    evolutionStatus,
  } = useChatStore();
  const { mode } = useSessionStore();
  const isInterruptible = isProcessing || isPaused;
  const isAgentMode = mode === 'agent.fast';
  const isTeamMode = mode === 'team';
  const isAutoHarnessMode = mode === 'auto_harness';
  const hasHistoryMessages = messages.length > 0;
  const modes: Array<{ value: AgentMode; label: string; icon: JSX.Element; hidden?: boolean }> = [
    { value: 'agent.plan', label: t('chat.modePlan'), icon: (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h3.75M9 15h3.75M9 18h3.75m3 .75H18a2.25 2.25 0 002.25-2.25V6.108c0-1.135-.845-2.098-1.976-2.192a48.424 48.424 0 00-1.123-.08m-5.801 0c-.065.21-.1.433-.1.664 0 .414.336.75.75.75h4.5a.75.75 0 00.75-.75 2.25 2.25 0 00-.1-.664m-5.8 0A2.251 2.251 0 0113.5 2.25H15c1.012 0 1.867.668 2.15 1.586m-5.8 0c-.376.023-.75.05-1.124.08C9.095 4.01 8.25 4.973 8.25 6.108V8.25m0 0H4.875c-.621 0-1.125.504-1.125 1.125v11.25c0 .621.504 1.125 1.125 1.125h9.75c.621 0 1.125-.504 1.125-1.125V9.375c0-.621-.504-1.125-1.125-1.125H8.25zM6.75 12h.008v.008H6.75V12zm0 3h.008v.008H6.75V15zm0 3h.008v.008H6.75V18z" />
      </svg>
    )},
    { value: 'agent.fast', label: t('chat.modeAgent'), icon: (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M11.42 15.17L17.25 21A2.652 2.652 0 0021 17.25l-5.877-5.877M11.42 15.17l2.496-3.03c.317-.384.74-.626 1.208-.766M11.42 15.17l-4.655 5.653a2.548 2.548 0 11-3.586-3.586l6.837-5.63m5.108-.233c.55-.164 1.163-.188 1.743-.14a4.5 4.5 0 004.486-6.336l-3.276 3.277a3.004 3.004 0 01-2.25-2.25l3.276-3.276a4.5 4.5 0 00-6.336 4.486c.091 1.076-.071 2.264-.904 2.95l-.102.085m-1.745 1.437L5.909 7.5H4.5L2.25 3.75l1.5-1.5L7.5 4.5v1.409l4.26 4.26m-1.745 1.437l1.745-1.437m6.615 8.206L15.75 15.75M4.867 19.125h.008v.008h-.008v-.008z" />
      </svg>
    )},
    { value: 'team', label: t('chat.modeAgentTeam'), icon: (
      <ClusterIcon className="w-4 h-4" />
    )},
    { value: 'auto_harness', label: t('chat.modeAutoHarness'), icon: (
      <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12a7.5 7.5 0 0015 0m-15 0a7.5 7.5 0 1115 0m-15 0H3m16.5 0H21m-1.5 0H12m-8.457 3.077l1.41-.513m14.095-5.13l1.41-.513M5.106 17.785l1.15-.964m11.69-9.765l1.15-.964m-3.093 5.25l.906-1.356m-6.768 1.356l.906-1.356M9 12H7.5m6.5 0H12m-1.5 0a1.5 1.5 0 103 0 1.5 1.5 0 00-3 0z" />
      </svg>
    ), hidden: true },
  ];

  const {
    isListening,
    interimTranscript,
    // startListening,
    stopListening,
    // isSupported: speechSupported,
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

  const imageInputDisabled = isListening || (isInterruptible && !isTeamMode);
  const readyAttachments = useMemo(
    () => attachments.filter((attachment) => attachment.status === 'ready' && attachment.base64Data),
    [attachments],
  );
  const hasUploadingAttachments = attachments.some((attachment) => attachment.status === 'uploading');
  const hasAttachmentErrors = attachments.some((attachment) => attachment.status === 'error');
  const readyMediaItems = useMemo(
    () => readyAttachments.map(attachmentToMediaItem),
    [readyAttachments],
  );

  useEffect(() => {
    if (!isListening && pendingVoiceText) {
      const finalText = (inputValue + pendingVoiceText).trim();
      if (finalText) {
        setInputValue(finalText);
        setPendingVoiceText('');

        setTimeout(() => {
          if (isTeamMode) {
            onSubmit(finalText);
          } else if (isInterruptible) {
            onInterrupt(finalText);
          } else {
            onSubmit(finalText);
          }
          setInputValue('');
          if (textareaRef.current) {
            textareaRef.current.style.height = 'auto';
          }
        }, 150);
      }
    }
  }, [isListening, pendingVoiceText, inputValue, isInterruptible, isTeamMode, onSubmit, onInterrupt, setInputValue]);

  useEffect(() => {
    return () => {
      if (autoSendTimeoutRef.current) {
        clearTimeout(autoSendTimeoutRef.current);
      }
      if (attachmentMenuTimerRef.current) {
        clearTimeout(attachmentMenuTimerRef.current);
      }
    };
  }, []);

  const pushAttachmentAlert = useCallback((message: string) => {
    const id = `${Date.now()}-${Math.random().toString(36).slice(2)}`;
    setAttachmentAlerts((prev) => [...prev, { id, message }].slice(-3));
  }, []);

  const dismissAttachmentAlert = useCallback((id: string) => {
    setAttachmentAlerts((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const updateAttachment = useCallback((id: string, update: Partial<AttachmentDraft>) => {
    setAttachments((prev) => prev.map((item) => (
      item.id === id ? { ...item, ...update } : item
    )));
  }, []);

  const removeAttachment = useCallback((id: string) => {
    setAttachments((prev) => prev.filter((item) => item.id !== id));
    setAttachmentMenuId((current) => (current === id ? null : current));
  }, []);

  const clearAttachments = useCallback(() => {
    setAttachments([]);
    setAttachmentAlerts([]);
    setAttachmentMenuId(null);
  }, []);

  const stopAttachmentMenuTimer = useCallback(() => {
    if (attachmentMenuTimerRef.current) {
      clearTimeout(attachmentMenuTimerRef.current);
      attachmentMenuTimerRef.current = null;
    }
  }, []);

  const startAttachmentMenuTimer = useCallback((id: string) => {
    stopAttachmentMenuTimer();
    attachmentMenuOpenedByLongPressRef.current = false;
    attachmentMenuTimerRef.current = setTimeout(() => {
      attachmentMenuOpenedByLongPressRef.current = true;
      setAttachmentMenuId(id);
    }, 520);
  }, [stopAttachmentMenuTimer]);

  const handleAttachmentRemoveClick = useCallback((id: string) => {
    if (attachmentMenuOpenedByLongPressRef.current || attachmentMenuId === id) {
      attachmentMenuOpenedByLongPressRef.current = false;
      return;
    }
    removeAttachment(id);
  }, [attachmentMenuId, removeAttachment]);

  const uploadAttachment = useCallback((attachment: AttachmentDraft) => {
    if (!attachment.file) return;
    const validationError = getImageValidationError(attachment.file);
    if (validationError) {
      pushAttachmentAlert(validationError);
      updateAttachment(attachment.id, { status: 'error', error: validationError });
      return;
    }
    updateAttachment(attachment.id, { status: 'uploading', error: undefined });
    void readImageFile(attachment.file).then(async (payload) => {
      if (!payload) {
        updateAttachment(attachment.id, {
          status: 'error',
          error: '上传失败，请重试',
        });
        return;
      }
      try {
        const persisted = await onPersistMedia('', [buildUploadMediaItem(attachment, payload)]);
        const persistedMediaItem = persisted.media_items?.[0];
        if (!persistedMediaItem || !pickString(persistedMediaItem.path)) {
          throw new Error('media.persist did not return image path');
        }
        updateAttachment(attachment.id, {
          ...payload,
          persistedMediaItem,
          status: 'ready',
          error: undefined,
        });
      } catch (error) {
        console.error('图片上传失败:', error);
        updateAttachment(attachment.id, {
          ...payload,
          status: 'error',
          error: '上传失败，请重试',
        });
      }
    });
  }, [onPersistMedia, pushAttachmentAlert, updateAttachment]);

  const retryAttachment = useCallback((attachment: AttachmentDraft) => {
    uploadAttachment(attachment);
  }, [uploadAttachment]);

  const appendImageFiles = useCallback((files: FileList | File[]) => {
    const selectedFiles = Array.from(files);
    if (!selectedFiles.length) return;
    const remainingSlots = Math.max(0, MAX_IMAGE_COUNT - attachments.length);
    if (!remainingSlots) {
      pushAttachmentAlert(`单次对话最多上传${MAX_IMAGE_COUNT}个附件。`);
      return;
    }

    const acceptedFiles = selectedFiles.slice(0, remainingSlots);
    const overflow = selectedFiles.length - acceptedFiles.length;
    if (overflow > 0) {
      pushAttachmentAlert(`单次对话最多上传${MAX_IMAGE_COUNT}个附件。`);
    }

    const drafts = acceptedFiles.reduce<AttachmentDraft[]>((items, file) => {
      const base = {
        id: makeAttachmentId(file),
        filename: file.name || `image-${Date.now()}`,
        mimeType: file.type || 'application/octet-stream',
        size: file.size,
        file,
      };
      const validationError = getImageValidationError(file);
      if (validationError) {
        pushAttachmentAlert(validationError);
        items.push({
          ...base,
          status: 'error',
          error: validationError,
        });
        return items;
      }
      items.push({
        ...base,
        status: 'uploading',
      });
      return items;
    }, []);

    if (!drafts.length) return;

    setAttachments((prev) => [...prev, ...drafts].slice(0, MAX_IMAGE_COUNT));
    drafts.forEach((draft) => {
      if (draft.status !== 'uploading' || !draft.file) return;
      uploadAttachment(draft);
    });
  }, [attachments.length, pushAttachmentAlert, uploadAttachment]);

  useEffect(() => {
    if (!isModeMenuOpen) return;

    const handlePointerDown = (event: PointerEvent) => {
      if (!modeMenuRef.current?.contains(event.target as Node)) {
        setIsModeMenuOpen(false);
      }
    };

    document.addEventListener('pointerdown', handlePointerDown);

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [isModeMenuOpen]);

  useEffect(() => {
    if (!attachmentMenuId) return;

    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (
        target?.closest('.chat-input-attachment-menu') ||
        target?.closest('.chat-input-attachment-remove')
      ) {
        return;
      }
      setAttachmentMenuId(null);
    };

    document.addEventListener('pointerdown', handlePointerDown);

    return () => {
      document.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [attachmentMenuId]);

  const handleSubmit = useCallback(() => {
    const trimmed = (inputValue + pendingVoiceText).trim();
    if ((!trimmed && readyMediaItems.length === 0) || hasUploadingAttachments || hasAttachmentErrors) return;
    if (isInterruptible && !isTeamMode && readyMediaItems.length > 0) return;

    if (isListening) {
      stopListening();
    }

    if (isTeamMode) {
      onSubmit(trimmed, readyMediaItems);
    } else if (isInterruptible) {
      if (isAgentMode && readyMediaItems.length === 0) {
        addToTaskQueue(trimmed);
      } else {
        onInterrupt(trimmed);
      }
    } else {
      onSubmit(trimmed, readyMediaItems);
    }
    setInputValue('');
    setPendingVoiceText('');
    setAttachments([]);
    setAttachmentAlerts([]);

    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto';
    }
  }, [
    inputValue,
    pendingVoiceText,
    readyMediaItems,
    hasUploadingAttachments,
    hasAttachmentErrors,
    isInterruptible,
    isListening,
    onSubmit,
    onInterrupt,
    stopListening,
    isAgentMode,
    isTeamMode,
    addToTaskQueue,
    setInputValue,
  ]);

  const trimmedDraft = (inputValue + pendingVoiceText).trim();
  const hasDraft = trimmedDraft.length > 0 || attachments.length > 0 || isListening;
  const isImageInterruptBlocked = isInterruptible && !isTeamMode && readyMediaItems.length > 0;
  const showStop = isProcessing && !isPaused && !hasDraft;
  const canSubmit = (hasDraft && !isImageInterruptBlocked && !hasUploadingAttachments && !hasAttachmentErrors) || showStop;

  const handleSendButtonClick = useCallback(() => {
    if (showStop) {
      onCancel();
      return;
    }

    handleSubmit();
  }, [handleSubmit, showStop, onCancel]);

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

  const handleFileInputChange = useCallback((event: ChangeEvent<HTMLInputElement>) => {
    const files = event.target.files;
    if (files) {
      void appendImageFiles(files);
    }
    event.target.value = '';
  }, [appendImageFiles]);

  const handlePaste = useCallback((event: ClipboardEvent<HTMLTextAreaElement>) => {
    const items = Array.from(event.clipboardData.items);
    const files = items
      .filter((item) => item.kind === 'file' && ACCEPTED_IMAGE_TYPES.has(item.type))
      .map((item) => item.getAsFile())
      .filter((file): file is File => Boolean(file));
    if (files.length) {
      event.preventDefault();
      void appendImageFiles(files);
    }
  }, [appendImageFiles]);

  const handleDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    const hasImage = Array.from(event.dataTransfer.items).some(
      (item) => item.kind === 'file' && ACCEPTED_IMAGE_TYPES.has(item.type)
    );
    if (!hasImage) return;
    event.preventDefault();
    setIsDraggingImage(true);
  }, []);

  const handleDragLeave = useCallback((event: DragEvent<HTMLDivElement>) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
      setIsDraggingImage(false);
    }
  }, []);

  const handleDrop = useCallback((event: DragEvent<HTMLDivElement>) => {
    setIsDraggingImage(false);
    const files = Array.from(event.dataTransfer.files).filter((file) => ACCEPTED_IMAGE_TYPES.has(file.type));
    if (!files.length) return;
    event.preventDefault();
    void appendImageFiles(files);
  }, [appendImageFiles]);

  // const handleVoiceStart = useCallback(() => {
  //   if (isListening) return;
  //   stopAllTts();
  //   startListening();
  // }, [isListening, startListening]);

  // const handleVoiceEnd = useCallback(() => {
  //   if (!isListening) return;
  //   stopListening();
  // }, [isListening, stopListening]);

  // const handleVoicePointerDown = useCallback(
  //   (e: ReactPointerEvent<HTMLButtonElement>) => {
  //     // 仅响应主按钮按压，避免右键/多指导致状态抖动
  //     if (e.pointerType === 'mouse' && e.button !== 0) return;
  //     if (activePointerIdRef.current !== null) return;
  //     e.preventDefault();
  //     activePointerIdRef.current = e.pointerId;
  //     isVoicePressingRef.current = true;
  //     e.currentTarget.setPointerCapture(e.pointerId);
  //     handleVoiceStart();
  //   },
  //   [handleVoiceStart]
  // );

  // const handleVoicePointerUp = useCallback(
  //   (e: ReactPointerEvent<HTMLButtonElement>) => {
  //     if (activePointerIdRef.current !== e.pointerId) return;
  //     e.preventDefault();
  //     activePointerIdRef.current = null;
  //     isVoicePressingRef.current = false;
  //     if (e.currentTarget.hasPointerCapture(e.pointerId)) {
  //       e.currentTarget.releasePointerCapture(e.pointerId);
  //     }
  //     handleVoiceEnd();
  //   },
  //   [handleVoiceEnd]
  // );

  // const handleVoicePointerCancel = useCallback(
  //   (e: ReactPointerEvent<HTMLButtonElement>) => {
  //     if (activePointerIdRef.current !== e.pointerId) return;
  //     activePointerIdRef.current = null;
  //     isVoicePressingRef.current = false;
  //     if (e.currentTarget.hasPointerCapture(e.pointerId)) {
  //       e.currentTarget.releasePointerCapture(e.pointerId);
  //     }
  //     handleVoiceEnd();
  //   },
  //   [handleVoiceEnd]
  // );

  const handleModeSwitch = useCallback(async (targetMode: AgentMode) => {
    if (mode === targetMode) return;
    
    // 切换任何模式时都 newSession
    if (hasHistoryMessages) {
      setPendingMode(targetMode);
      setShowModeSwitchModal(true);
    } else {
      await onNewSession();
      onSwitchMode(targetMode);
    }
  }, [mode, hasHistoryMessages, onNewSession, onSwitchMode]);

  const handleModeSelect = useCallback(async (targetMode: AgentMode) => {
    setIsModeMenuOpen(false);
    await handleModeSwitch(targetMode);
  }, [handleModeSwitch]);

  const confirmModeSwitch = useCallback(async () => {
    if (pendingMode) {
      setShowModeSwitchModal(false);
      await onNewSession();
      onSwitchMode(pendingMode);
      setPendingMode(null);
    }
  }, [pendingMode, onNewSession, onSwitchMode]);

  const cancelModeSwitch = useCallback(() => {
    setShowModeSwitchModal(false);
    setPendingMode(null);
  }, []);

  useEffect(() => {
    setIsModeMenuOpen(false);
  }, [mode]);

  const displayValue = isListening
    ? inputValue + pendingVoiceText + interimTranscript
    : inputValue + pendingVoiceText;

  const currentMode = modes.find((item) => item.value === mode) ?? modes[0];
  const evolutionLabel = getEvolutionPillLabel(mode, evolutionStatus, t);

  return (
    <>
      <div className="chat-input-frame">
        {attachmentAlerts.length > 0 && (
          <div className="chat-input-local-alerts" role="status" aria-live="polite">
            {attachmentAlerts.map((alert) => (
              <div className="chat-input-local-alert" key={alert.id}>
                <CircleX size={16} strokeWidth={2.2} />
                <span>{alert.message}</span>
                <button
                  type="button"
                  onClick={() => dismissAttachmentAlert(alert.id)}
                  aria-label="关闭提示"
                >
                  <X size={15} strokeWidth={2} />
                </button>
              </div>
            ))}
          </div>
        )}

        <div
          className={cx(
            'chat-input-container',
            isModeMenuOpen && 'chat-input-container--menu-open',
            isListening && 'chat-input-container--recording',
            isDraggingImage && 'chat-input-container--dragging',
          )}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
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

      {attachments.length > 0 && (
        <div className="chat-input-attachment-panel">
          <div
            className={cx(
              'chat-input-attachment-grid',
              attachmentMenuId && 'chat-input-attachment-grid--menu-open',
            )}
          >
            {attachments.map((attachment) => (
              <div
                className={cx(
                  'chat-input-attachment-card',
                  attachment.status === 'error' && 'chat-input-attachment-card--error',
                  attachment.status === 'uploading' && 'chat-input-attachment-card--uploading',
                )}
                key={attachment.id}
              >
                <div className="chat-input-attachment-preview" aria-hidden="true">
                  {attachment.previewUrl ? (
                    <img src={attachment.previewUrl} alt="" />
                  ) : (
                    <FileImage size={18} strokeWidth={1.8} />
                  )}
                </div>
                <div className="chat-input-attachment-main">
                  <div className="chat-input-attachment-name" title={attachment.filename}>
                    {attachment.filename}
                  </div>
                  <div className="chat-input-attachment-meta">
                    {attachment.status === 'uploading' ? (
                      <>
                        <Loader2 className="chat-input-attachment-spin" size={12} strokeWidth={2} />
                        <span>上传中...</span>
                      </>
                    ) : attachment.status === 'error' ? (
                      <>
                        <span
                          className="chat-input-attachment-status-error"
                          title={attachment.error || '上传失败'}
                        >
                          上传失败
                        </span>
                        {attachment.file && (
                          <button
                            type="button"
                            className="chat-input-attachment-retry"
                            onClick={() => retryAttachment(attachment)}
                          >
                            重试
                          </button>
                        )}
                      </>
                    ) : (
                      <>
                        <span>{attachment.mimeType.split('/')[1]?.toUpperCase() || 'IMAGE'}</span>
                        <span>{formatAttachmentSize(attachment.size)}</span>
                      </>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  className="chat-input-attachment-remove"
                  onPointerDown={() => startAttachmentMenuTimer(attachment.id)}
                  onPointerUp={stopAttachmentMenuTimer}
                  onPointerCancel={stopAttachmentMenuTimer}
                  onPointerLeave={stopAttachmentMenuTimer}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    stopAttachmentMenuTimer();
                    setAttachmentMenuId(attachment.id);
                  }}
                  onClick={() => handleAttachmentRemoveClick(attachment.id)}
                  title="删除，长按显示更多操作"
                  aria-label="删除附件"
                >
                  <X size={12} strokeWidth={2} />
                </button>
                {attachmentMenuId === attachment.id && (
                  <div className="chat-input-attachment-menu" role="menu">
                    <button
                      type="button"
                      role="menuitem"
                      onClick={() => removeAttachment(attachment.id)}
                    >
                      删除
                    </button>
                    <button
                      type="button"
                      role="menuitem"
                      onClick={clearAttachments}
                    >
                      清空附件
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      <textarea
        ref={textareaRef}
        value={displayValue}
        onChange={(e) => setInputValue(e.target.value)}
        onKeyDown={handleKeyDown}
        onCompositionStart={() => { isComposingRef.current = true; }}
        onCompositionEnd={() => { isComposingRef.current = false; }}
        onInput={handleInput}
        onPaste={handlePaste}
        placeholder={
          isListening
            ? t('chat.placeholderVoice')
            : isTeamMode
              ? isInterruptible && !isPaused
              ? t('chat.placeholderTeamModeProcessing')
              : t('chat.placeholderTeamMode')
              : isAutoHarnessMode
                ? t('autoHarness.inputPlaceholder')
                : isAgentMode && isInterruptible
                  ? t('chat.placeholderProcessingQueue')
                  : isInterruptible
                    ? t('chat.placeholderProcessing')
                    : t('chat.placeholder')
        }
        className="chat-input-textarea"
        rows={1}
        data-testid="chat-input"
      />

      <div className="chat-input-toolbar">
        <div className="chat-input-toolbar-left">
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp,image/gif"
            multiple
            className="hidden"
            onChange={handleFileInputChange}
          />
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={imageInputDisabled}
            className={cx(
              'chat-input-btn chat-input-btn--add-file',
              imageInputDisabled && 'chat-input-btn--disabled',
            )}
            title={imageInputDisabled ? t('chat.addImageDisabled') : t('chat.addImage')}
            aria-label={imageInputDisabled ? t('chat.addImageDisabled') : t('chat.addImage')}
          >
            <Plus className="chat-input-btn-icon" strokeWidth={1.8} />
          </button>
          <div
            ref={modeMenuRef}
            className={clsx(
              'chat-mode-select',
              isModeMenuOpen && 'chat-mode-select--open',
            )}
          >
            <button
              type="button"
              className="chat-mode-select__trigger"
              onClick={() => setIsModeMenuOpen((open) => !open)}
              aria-haspopup="menu"
              aria-expanded={isModeMenuOpen}
              data-testid={`chat-mode-${currentMode.value}`}
            >
              <span className="chat-mode-select__value">
                <span className="chat-mode-select__icon" aria-hidden="true">
                  {currentMode.icon}
                </span>
                <span className="chat-mode-select__label">{currentMode.label}</span>
              </span>
              <svg className="chat-mode-select__chevron" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={1.8} aria-hidden="true">
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 8l4 4 4-4" />
              </svg>
            </button>

            {isModeMenuOpen && (
              <div
                className="chat-mode-select__menu"
                role="menu"
              >
                {modes.filter((m) => !m.hidden).map((m) => (
                  <button
                    type="button"
                    key={m.value}
                    onClick={() => void handleModeSelect(m.value)}
                    className={clsx(
                      'chat-mode-select__option',
                      mode === m.value && 'chat-mode-select__option--active',
                    )}
                    role="menuitemradio"
                    aria-checked={mode === m.value}
                    data-testid={`chat-mode-option-${m.value}`}
                  >
                    <span className="chat-mode-select__option-main">
                      <span className="chat-mode-select__icon" aria-hidden="true">
                        {m.icon}
                      </span>
                      <span className="chat-mode-select__label">{m.label}</span>
                    </span>
                    {mode === m.value && (
                      <svg className="chat-mode-select__check" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M5 10.5l3 3L15 6.5" />
                      </svg>
                    )}
                  </button>
                ))}
              </div>
            )}
          </div>
          {evolutionLabel && (
            <div className="chat-input-evolution-pill" title={evolutionLabel}>
              <span className="chat-input-evolution-pill__dot" />
              <span className="chat-input-evolution-pill__label">{evolutionLabel}</span>
            </div>
          )}
        </div>

        <div className="chat-input-actions">
          {/* {speechSupported && (
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
          )} */}

          <ModelSelector />

          <button
            type="button"
            onClick={handleSendButtonClick}
            disabled={!canSubmit}
            className={cx(
              'chat-input-btn chat-input-btn--send',
              showStop && 'chat-input-btn--stop',
              canSubmit ? 'chat-input-btn--send-active' : 'chat-input-btn--disabled',
            )}
            title={showStop ? t('chat.stop') : t('chat.send')}
            data-testid="chat-send"
          >
            {showStop ? (
              <Square className="chat-input-btn-icon" fill="currentColor" strokeWidth={1.8} aria-hidden="true" />
            ) : (
              <img
                className="chat-input-btn-icon chat-input-btn-icon--image"
                src={canSubmit ? sendActiveIcon : sendIcon}
                alt=""
                aria-hidden="true"
              />
            )}
          </button>
        </div>
      </div>

      {showModeSwitchModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
          <div className="bg-bg border border-border rounded-xl p-4 shadow-lg max-w-sm w-full mx-4">
            <h3 className="text-base font-medium text-text mb-2">{t('chat.modeSwitchTitle')}</h3>
            <p className="text-sm text-text-muted mb-4">{t('chat.modeSwitchConfirm')}</p>
            <div className="flex justify-end gap-2">
              <button
                type="button"
                onClick={cancelModeSwitch}
                className="px-3 py-1.5 text-sm rounded-lg bg-secondary text-text-muted hover:bg-secondary/80"
              >
                {t('common.cancel')}
              </button>
              <button
                type="button"
                onClick={confirmModeSwitch}
                className="px-3 py-1.5 text-sm rounded-lg bg-accent text-white hover:bg-accent/80"
              >
                {t('common.confirm')}
              </button>
            </div>
          </div>
        </div>
      )}
        </div>
      </div>
    </>
  );
}

function ModelSelector() {
  const { chatAvailableModels, selectedModelName, setSelectedModelName } = useSessionStore();
  const { t } = useTranslation();

  if (chatAvailableModels.length === 0) return null;

  if (chatAvailableModels.length === 1) {
    return (
      <span className="chat-model-inline">
        <span
          className="chat-model-inline__name"
          title={chatAvailableModels[0].model_name}
        >
          {chatAvailableModels[0].alias || chatAvailableModels[0].model_name}
        </span>
      </span>
    );
  }

  return (
    <span className="chat-model-inline">
      <select
        value={selectedModelName ?? ''}
        onChange={(e) => setSelectedModelName(e.target.value)}
        title={t('chat.modelSelector.tooltip')}
        className="chat-model-selector"
        data-testid="chat-model-selector"
      >
        {chatAvailableModels.map((m, idx) => (
          <option key={`${m.model_name}-${idx}`} value={m.alias || m.model_name}>
            {m.alias ? `${m.alias} (${m.model_name})` : m.model_name}
          </option>
        ))}
      </select>
    </span>
  );
}

function cx(...classes: (string | boolean | undefined | null)[]) {
  return classes.filter(Boolean).join(' ');
}
