import { useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, UploadCloud, Info, FileArchive } from 'lucide-react';

interface UploadFileCreateModalProps {
  onCancel: () => void;
  onConfirm: (fileName: string) => void;
}

const ACCEPTED_EXTENSIONS = ['.zip', '.tar'];

function isAcceptedFile(file: File): boolean {
  const name = file.name.toLowerCase();
  return ACCEPTED_EXTENSIONS.some((ext) => name.endsWith(ext));
}

function formatFileSize(bytes: number): string {
  if (bytes <= 0) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(1024));
  const size = bytes / Math.pow(1024, i);
  return `${size.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

// 对应高保真 3.5 上传文件创建。两份后端接口文档（connector.*/plugin_packages.*）都没有
// "上传文件包创建插件"这个接口——不只是没设计安装/卸载，是这个入口本身完全没有后端对应，
// 需要补进 backend-requests.md。onConfirm 目前只是把文件名回传给上层，上层不会真的创建成功。
// 2026-08-20 用户反馈两点补上：① 原来的"拖动文件到此处"是假的——只接了 <input type=file> 的
// click 上传，没有任何 onDrop/onDragOver 处理，拖文件上去完全没反应；② 选中文件后只有框内一行
// 文字从提示语换成文件名，视觉反馈太弱。这版补上真实拖拽 + 点击/拖拽都做 .zip/.tar 后缀校验
// （不通过就红框+错误提示，不接受这个文件）+ 选中后换成图标+文件名+大小+移除按钮的文件卡片。
export function UploadFileCreateModal({ onCancel, onConfirm }: UploadFileCreateModalProps) {
  const { t } = useTranslation();
  const [file, setFile] = useState<File | null>(null);
  const [invalid, setInvalid] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  function handleFile(candidate: File | undefined) {
    if (!candidate) return;
    if (!isAcceptedFile(candidate)) {
      setInvalid(true);
      setFile(null);
      return;
    }
    setInvalid(false);
    setFile(candidate);
  }

  function handleRemove() {
    setFile(null);
    setInvalid(false);
    if (inputRef.current) inputRef.current.value = '';
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-overlay-cron-dialog">
      <div className="relative w-[520px] rounded-2xl bg-card p-6 shadow-xl">
        <div className="mb-4 flex items-center justify-between">
          <h2 className="text-[16px] font-semibold leading-6 text-text">{t('connectorMarket.create.withUpload')}</h2>
          <button type="button" onClick={onCancel} className="text-text-muted hover:text-text">
            <X size={18} />
          </button>
        </div>

        <div className="mb-4 flex gap-2 rounded-lg bg-accent-subtle px-3 py-2.5 text-[12px] leading-[18px] text-text">
          <Info size={14} className="mt-0.5 shrink-0 text-[color:var(--color-chat-accent)]" />
          <span>{t('connectorMarket.upload.hint')}</span>
        </div>

        <div
          onDragOver={(event) => {
            event.preventDefault();
            setDragOver(true);
          }}
          onDragLeave={() => setDragOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setDragOver(false);
            handleFile(event.dataTransfer.files?.[0]);
          }}
          className={`flex h-40 flex-col items-center justify-center gap-2 rounded-xl border border-dashed bg-bg text-text-muted transition-colors ${
            invalid
              ? 'border-danger'
              : dragOver
                ? 'border-[color:var(--color-chat-accent)]'
                : 'border-border-strong hover:border-border-hover'
          }`}
        >
          {file ? (
            <div className="flex w-full items-center gap-2.5 px-5">
              <FileArchive size={22} className="shrink-0 text-text-muted" />
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] text-text" title={file.name}>{file.name}</p>
                <p className="text-[11px] text-text-muted">{formatFileSize(file.size)}</p>
              </div>
              <button type="button" onClick={handleRemove} className="shrink-0 text-text-muted hover:text-text">
                <X size={16} />
              </button>
            </div>
          ) : (
            <label className="flex h-full w-full cursor-pointer flex-col items-center justify-center gap-2">
              <UploadCloud size={22} />
              <span className="text-[13px]">{t('connectorMarket.upload.dropHint')}</span>
              <input
                ref={inputRef}
                type="file"
                accept=".zip,.tar"
                className="hidden"
                onChange={(event) => handleFile(event.target.files?.[0])}
              />
            </label>
          )}
        </div>
        {invalid && <p className="mt-1.5 text-[11px] text-danger">{t('connectorMarket.upload.invalidType')}</p>}

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="rounded-lg border border-border px-4 py-1.5 text-[13px] text-text hover:border-border-hover">
            {t('connectorMarket.common.cancel')}
          </button>
          <button
            type="button"
            onClick={() => onConfirm(file?.name ?? 'plugin-package.zip')}
            className="rounded-lg bg-text px-4 py-1.5 text-[13px] text-text-inverse"
          >
            {t('connectorMarket.common.confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}
