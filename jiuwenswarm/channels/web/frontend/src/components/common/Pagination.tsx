/**
 * 通用分页条：上一页/下一页 + 页码指示 + 可选总数。
 * 供「我的技能」列表、企业技能源面板等列表复用。
 */
import { useTranslation } from "react-i18next";

interface PaginationProps {
  page: number;
  totalPages: number;
  total?: number;
  onPageChange: (page: number) => void;
  className?: string;
}

export function Pagination({
  page,
  totalPages,
  total,
  onPageChange,
  className = "",
}: PaginationProps) {
  const { t } = useTranslation();
  if (totalPages <= 1) return null;
  return (
    <div className={`flex items-center justify-end gap-3 text-sm text-text-muted flex-shrink-0 ${className}`}>
      {total != null && <span>{t("common.pagination.total", { count: total })}</span>}
      <button
        type="button"
        onClick={() => onPageChange(page - 1)}
        disabled={page <= 1}
        aria-label={t("common.pagination.prev")}
        className="px-3 py-1 rounded-md border border-border hover:bg-secondary/50 disabled:text-text-muted disabled:cursor-not-allowed"
      >
        ‹
      </button>
      <span>{page} / {totalPages}</span>
      <button
        type="button"
        onClick={() => onPageChange(page + 1)}
        disabled={page >= totalPages}
        aria-label={t("common.pagination.next")}
        className="px-3 py-1 rounded-md border border-border hover:bg-secondary/50 disabled:text-text-muted disabled:cursor-not-allowed"
      >
        ›
      </button>
    </div>
  );
}
