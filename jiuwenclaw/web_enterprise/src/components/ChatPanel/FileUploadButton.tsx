import { useCallback, useRef, useState, ChangeEvent } from 'react';
import { useTranslation } from 'react-i18next';
import { uploadFileToObs } from '../../services/obsUpload';
import { ChatSendFile } from '../../types';
import clsx from 'clsx';

interface FileUploadButtonProps {
  disabled?: boolean;
  onUploaded: (file: ChatSendFile) => void;
  onError?: (message: string) => void;
}

export function FileUploadButton({
  disabled = false,
  onUploaded,
  onError,
}: FileUploadButtonProps) {
  const { t } = useTranslation();
  const inputRef = useRef<HTMLInputElement>(null);
  const [isUploading, setIsUploading] = useState(false);

  const handleClick = useCallback(() => {
    if (disabled || isUploading) return;
    inputRef.current?.click();
  }, [disabled, isUploading]);

  const handleFileChange = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const selected = event.target.files;
      event.target.value = '';
      if (!selected?.length) return;

      setIsUploading(true);
      try {
        for (const file of Array.from(selected)) {
          const uploaded = await uploadFileToObs(file);
          onUploaded(uploaded);
        }
      } catch (error) {
        const raw = error instanceof Error ? error.message : 'upload_failed';
        const message =
          raw === 'file_too_large'
            ? t('chat.fileUpload.errors.tooLarge')
            : raw === 'empty_file'
              ? t('chat.fileUpload.errors.empty')
              : t('chat.fileUpload.errors.failed', { message: raw });
        onError?.(message);
      } finally {
        setIsUploading(false);
      }
    },
    [onError, onUploaded, t]
  );

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={handleFileChange}
        data-testid="chat-file-input"
      />
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || isUploading}
        className={clsx(
          'chat-input-btn',
          (disabled || isUploading) && 'chat-input-btn--disabled',
          isUploading && 'chat-input-btn--uploading'
        )}
        title={isUploading ? t('chat.fileUpload.uploading') : t('chat.fileUpload.button')}
        data-testid="chat-file-upload"
      >
        {isUploading ? (
          <svg
            className="chat-input-btn-icon chat-input-btn-icon--spin"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            strokeWidth={1.8}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182"
            />
          </svg>
        ) : (
          <svg
            className="chat-input-btn-icon"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
            strokeWidth={1.8}
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              d="M18.375 12.739l-7.693 7.693a4.5 4.5 0 01-6.364-6.364l10.94-10.94A3 3 0 1119.5 7.372L8.552 18.32m.009-.01l-.01.01m5.699-9.941l-7.81 7.81a1.5 1.5 0 002.112 2.13"
            />
          </svg>
        )}
      </button>
    </>
  );
}
