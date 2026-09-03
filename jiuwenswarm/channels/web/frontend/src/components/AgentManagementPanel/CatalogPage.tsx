import { useCallback, useLayoutEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import type { AgentCatalogItem, RequestStatus } from '../../features/agentManagement';
import { DefinitionCard } from './DefinitionCard';

const PAGE_SIZE = 15;
const CATEGORIES = ['ProductDevelopment', 'Marketing', 'Efficiency', 'DataAnalysis', 'ContentCreation', 'SafetyCompliance', 'Communication', 'Other'];

type CatalogPageProps = {
  scope: 'catalog' | 'mine';
  items: AgentCatalogItem[];
  totalItems: number;
  page: number;
  totalPages: number;
  query: string;
  category: string;
  status: RequestStatus;
  error: string | null;
  busyId: string | null;
  onCategoryChange: (value: string) => void;
  onPageChange: (page: number) => void;
  onRetry: () => void;
  onOpen: (id: string) => void;
  onUse: (id: string) => void;
  onReconnect: (id: string) => void;
  onInstall: (id: string) => void;
  onUninstall: (id: string) => void;
  onCreate: () => void;
};

type CategoryRowProps = {
  category: string;
  onChange: (value: string) => void;
};

function CategoryRow({ category, onChange }: CategoryRowProps) {
  const { t } = useTranslation();
  const scrollRef = useRef<HTMLDivElement>(null);
  const [canScrollLeft, setCanScrollLeft] = useState(false);
  const [canScrollRight, setCanScrollRight] = useState(false);

  const updateScrollState = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    setCanScrollLeft(el.scrollLeft > 1);
    setCanScrollRight(el.scrollLeft < el.scrollWidth - el.clientWidth - 1);
  }, []);

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    updateScrollState();
    if (typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(updateScrollState);
    observer.observe(el);
    return () => observer.disconnect();
  }, [updateScrollState]);

  const scrollByPage = (direction: 1 | -1) => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollBy({ left: direction * el.clientWidth * 0.8, behavior: 'smooth' });
  };

  return (
    <div
      className={`agent-management-category-row${canScrollLeft ? ' is-scroll-left' : ''}${canScrollRight ? ' is-scroll-right' : ''}`}
    >
      <button
        type="button"
        className="agent-management-category-scroll agent-management-category-scroll--prev"
        aria-label={t('agentManagement.categoryScrollPrev')}
        onClick={() => scrollByPage(-1)}
        data-hidden={!canScrollLeft}
      >
        <ChevronLeft size={16} aria-hidden="true" />
      </button>
      <div
        className="agent-management-category-row__viewport"
        ref={scrollRef}
        role="tablist"
        aria-label={t('agentManagement.categoryLabel')}
        onScroll={updateScrollState}
      >
        <button
          type="button"
          role="tab"
          aria-selected={!category}
          className={`agent-management-category${!category ? ' is-active' : ''}`}
          onClick={() => onChange('')}
        >
          {t('agentManagement.categoryAll')}
        </button>
        {CATEGORIES.map(item => (
          <button
            key={item}
            type="button"
            role="tab"
            aria-selected={category === item}
            className={`agent-management-category${category === item ? ' is-active' : ''}`}
            onClick={() => onChange(item)}
          >
            {t(`agentManagement.categories.${item}`, { defaultValue: item })}
          </button>
        ))}
      </div>
      <button
        type="button"
        className="agent-management-category-scroll agent-management-category-scroll--next"
        aria-label={t('agentManagement.categoryScrollNext')}
        onClick={() => scrollByPage(1)}
        data-hidden={!canScrollRight}
      >
        <ChevronRight size={16} aria-hidden="true" />
      </button>
    </div>
  );
}

export function CatalogPage({
  scope,
  items,
  totalItems,
  page,
  totalPages,
  query,
  category,
  status,
  error,
  busyId,
  onCategoryChange,
  onPageChange,
  onRetry,
  onOpen,
  onUse,
  onReconnect,
  onInstall,
  onUninstall,
  onCreate,
}: CatalogPageProps) {
  const { t } = useTranslation();
  const isMine = scope === 'mine';
  const isEmpty = status === 'success' && totalItems === 0;
  const hasQuery = query.trim().length > 0 || Boolean(category);

  return (
    <>
      {!isMine ? (
        <div className="agent-management-toolbar">
          <CategoryRow category={category} onChange={onCategoryChange} />
        </div>
      ) : null}

      <div className="min-h-0 flex-1 overflow-y-auto" data-testid="agent-management-catalog-content">
        {status === 'loading' && totalItems === 0 ? null : status === 'error' ? (
          <div className="agent-management-state agent-management-state--error" role="alert">
            <p>{error || t('agentManagement.states.loadError')}</p>
            <button type="button" className="agent-management-button agent-management-button--secondary" onClick={onRetry}>
              {t('common.retry')}
            </button>
          </div>
        ) : isEmpty ? (
          <div className="agent-management-state">
            <p>{hasQuery ? t('agentManagement.states.noMatch') : t(isMine ? 'agentManagement.states.mineEmpty' : 'agentManagement.states.catalogEmpty')}</p>
            {isMine && !hasQuery ? (
              <button type="button" className="agent-management-button agent-management-button--primary" onClick={onCreate}>
                {t('agentManagement.actions.createFirst')}
              </button>
            ) : null}
          </div>
        ) : (
          <>
            <div className="card-grid-auto" style={{ paddingTop: '16px' }}>
              {items.map(item => (
                <DefinitionCard
                  key={item.id}
                  item={item}
                  scope={scope}
                  busy={busyId === item.id}
                  onOpen={onOpen}
                  onUse={onUse}
                  onReconnect={onReconnect}
                  onInstall={onInstall}
                  onUninstall={onUninstall}
                />
              ))}
            </div>
            {totalPages > 1 ? (
              <div className="agent-management-pagination" aria-label={t('agentManagement.pagination.label')}>
                <span>
                  {t('agentManagement.pagination.range', { start: (page - 1) * PAGE_SIZE + 1, end: Math.min(page * PAGE_SIZE, totalItems), total: totalItems })}
                </span>
                <div className="agent-management-pagination__buttons">
                  <button type="button" disabled={page <= 1} onClick={() => onPageChange(page - 1)} aria-label={t('agentManagement.pagination.previous')}>
                    <ChevronLeft size={16} aria-hidden="true" />
                  </button>
                  <span>{t('agentManagement.pagination.page', { page, total: totalPages })}</span>
                  <button type="button" disabled={page >= totalPages} onClick={() => onPageChange(page + 1)} aria-label={t('agentManagement.pagination.next')}>
                    <ChevronRight size={16} aria-hidden="true" />
                  </button>
                </div>
              </div>
            ) : null}
          </>
        )}
      </div>
    </>
  );
}

export { PAGE_SIZE };
