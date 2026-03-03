import { useEffect } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface HeartbeatMessageModalProps {
  open: boolean;
  message: string;
  onClose: () => void;
}

export function HeartbeatMessageModal({ open, message, onClose }: HeartbeatMessageModalProps) {
  useEffect(() => {
    if (!open) {
      return;
    }

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        onClose();
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        type="button"
        className="absolute inset-0 bg-black/60"
        onClick={onClose}
        aria-label="关闭心跳详情弹窗"
      />
      <div className="relative w-full max-w-3xl max-h-[85vh] overflow-hidden rounded-xl border border-border bg-card shadow-2xl animate-rise">
        <div className="flex items-center justify-between gap-3 px-5 py-3 border-b border-border bg-panel">
          <div>
            <h3 className="text-base font-semibold text-text">心跳消息</h3>
            <p className="text-xs text-text-muted">消息内容详情</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="px-2.5 py-1.5 rounded-md border border-border bg-secondary/50 text-text-muted hover:text-text hover:bg-secondary transition-colors"
          >
            关闭
          </button>
        </div>
        <div className="p-5 overflow-auto max-h-[calc(85vh-64px)]">
          <article className="chat-text max-w-none">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message || '无详细内容'}
            </ReactMarkdown>
          </article>
        </div>
      </div>
    </div>
  );
}
