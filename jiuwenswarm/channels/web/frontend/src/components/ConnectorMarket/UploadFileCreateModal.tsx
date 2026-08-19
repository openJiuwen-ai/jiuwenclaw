import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { X, UploadCloud, Info } from 'lucide-react';

interface UploadFileCreateModalProps {
  onCancel: () => void;
  onConfirm: (fileName: string) => void;
}

// 对应高保真 3.5 上传文件创建。两份后端接口文档（connector.*/plugin_packages.*）都没有
// "上传文件包创建插件"这个接口——不只是没设计安装/卸载，是这个入口本身完全没有后端对应，
// 需要补进 backend-requests.md。onConfirm 目前只是把文件名回传给上层，上层不会真的创建成功。
export function UploadFileCreateModal({ onCancel, onConfirm }: UploadFileCreateModalProps) {
  const { t } = useTranslation();
  const [fileName, setFileName] = useState<string | null>(null);

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

        <label className="flex h-40 cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border-strong bg-bg text-text-muted hover:border-border-hover">
          <UploadCloud size={22} />
          <span className="text-[13px]">{fileName ?? t('connectorMarket.upload.dropHint')}</span>
          <input type="file" className="hidden" onChange={(event) => setFileName(event.target.files?.[0]?.name ?? null)} />
        </label>

        <div className="mt-5 flex justify-end gap-2">
          <button type="button" onClick={onCancel} className="rounded-lg border border-border px-4 py-1.5 text-[13px] text-text hover:border-border-hover">
            {t('connectorMarket.common.cancel')}
          </button>
          <button
            type="button"
            onClick={() => onConfirm(fileName ?? 'plugin-package.zip')}
            className="rounded-lg bg-text px-4 py-1.5 text-[13px] text-text-inverse"
          >
            {t('connectorMarket.common.confirm')}
          </button>
        </div>
      </div>
    </div>
  );
}
