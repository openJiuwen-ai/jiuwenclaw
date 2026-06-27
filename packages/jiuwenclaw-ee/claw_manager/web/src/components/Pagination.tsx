import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';

export type PagerChangeHandler = (page: number, pageSize: number) => void;

export interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  pageSizeOptions?: number[];
  loading?: boolean;
  error?: string | null;
  onChange: PagerChangeHandler;
}

const DEFAULT_PAGE_SIZE_OPTIONS = [10, 20, 30, 40, 50];

function getVisiblePages(current: number, total: number): Array<number | 'ellipsis'> {
  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1);
  }

  const pages: Array<number | 'ellipsis'> = [1];
  const left = Math.max(2, current - 1);
  const right = Math.min(total - 1, current + 1);

  if (left > 2) {
    pages.push('ellipsis');
  }

  for (let i = left; i <= right; i += 1) {
    pages.push(i);
  }

  if (right < total - 1) {
    pages.push('ellipsis');
  }

  pages.push(total);
  return pages;
}

export function Pagination({
  page,
  pageSize,
  total,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  loading = false,
  error = null,
  onChange,
}: PaginationProps) {
  const { t } = useTranslation();
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages);
  const [jumpPage, setJumpPage] = useState(String(safePage));

  useEffect(() => {
    setJumpPage(String(safePage));
  }, [safePage]);

  const visiblePages = useMemo(() => getVisiblePages(safePage, totalPages), [safePage, totalPages]);

  const handleGoToPage = () => {
    const parsedPage = Number(jumpPage);
    if (Number.isNaN(parsedPage)) {
      setJumpPage(String(safePage));
      return;
    }

    const targetPage = Math.min(totalPages, Math.max(1, Math.floor(parsedPage)));
    onChange(targetPage, pageSize);
    setJumpPage(String(targetPage));
  };

  const handleJumpInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      handleGoToPage();
    }
  };

  if (loading || error) {
    return null;
  }

  if (total <= 0) {
    return null;
  }

  return (
    <div className="flex flex-wrap items-center justify-end gap-3 pt-2 text-xs text-muted">
      <span className="text-[var(--text)]">{t('common.pagination.total', { total })}</span>

      <div className="flex items-center gap-2">
        <select
          value={pageSize}
          onChange={(e) => onChange(1, Number(e.target.value))}
          className="select !w-auto py-1 pl-2 pr-7 text-xs"
          aria-label={t('common.pagination.itemsPerPage')}
        >
          {pageSizeOptions.map((opt) => (
            <option key={opt} value={opt}>
              {opt}
            </option>
          ))}
        </select>
        <span>{t('common.pagination.itemsPerPage')}</span>
      </div>

      <nav className="flex items-center gap-1" aria-label={t('common.pagination.nav')}>
        <button
          type="button"
          className="btn sm min-w-[2rem] px-2"
          disabled={safePage <= 1}
          onClick={() => onChange(safePage - 1, pageSize)}
          aria-label={t('common.pagination.prev')}
          title={t('common.pagination.prev')}
        >
          &lt;
        </button>

        {visiblePages.map((item, index) =>
          item === 'ellipsis' ? (
            <span key={`ellipsis-${index}`} className="px-1 text-muted select-none">
              …
            </span>
          ) : (
            <button
              key={item}
              type="button"
              className={`btn sm min-w-[2rem] px-2 ${item === safePage ? 'primary' : 'ghost'}`}
              aria-current={item === safePage ? 'page' : undefined}
              onClick={() => onChange(item, pageSize)}
            >
              {item}
            </button>
          ),
        )}

        <button
          type="button"
          className="btn sm min-w-[2rem] px-2"
          disabled={safePage >= totalPages}
          onClick={() => onChange(safePage + 1, pageSize)}
          aria-label={t('common.pagination.next')}
          title={t('common.pagination.next')}
        >
          &gt;
        </button>
      </nav>

      <div className="flex items-center gap-2">
        <input
          type="number"
          value={jumpPage}
          onChange={(e) => setJumpPage(e.target.value)}
          onKeyDown={handleJumpInputKeyDown}
          min={1}
          max={totalPages}
          className="input !w-14 py-1 px-2 text-center text-xs"
          aria-label={t('common.pagination.jumpTo')}
        />
        <button type="button" className="btn sm" onClick={handleGoToPage}>
          {t('common.pagination.go')}
        </button>
      </div>
    </div>
  );
}
