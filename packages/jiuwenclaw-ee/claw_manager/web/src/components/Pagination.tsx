interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  onChange: (page: number) => void;
}

export function Pagination({ page, pageSize, total, onChange }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  return (
    <div className="flex items-center justify-end gap-2 text-xs text-muted">
      <span>
        共 {total} 条 · 第 {page}/{totalPages} 页
      </span>
      <button className="btn sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
        上一页
      </button>
      <button
        className="btn sm"
        disabled={page >= totalPages}
        onClick={() => onChange(page + 1)}
      >
        下一页
      </button>
    </div>
  );
}
