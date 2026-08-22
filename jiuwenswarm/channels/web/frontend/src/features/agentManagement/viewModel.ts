import type { AgentCatalogItem, AgentDetail, DefinitionFileEntry } from './types';

export type CatalogScope = 'catalog' | 'mine';

export type CatalogViewModel = {
  items: AgentCatalogItem[];
  totalItems: number;
  page: number;
  totalPages: number;
};

export function findFirstPreviewableFile(entries: DefinitionFileEntry[]): string | null {
  const preferred = entries.find(
    entry => entry.visible !== false && entry.kind === 'file' && entry.relativePath.toLowerCase().startsWith('persona/') && entry.previewable,
  );
  if (preferred) return preferred.relativePath;

  for (const entry of entries) {
    if (entry.visible === false) continue;
    if (entry.kind === 'file' && entry.previewable) return entry.relativePath;
    const nested = entry.children ? findFirstPreviewableFile(entry.children) : null;
    if (nested) return nested;
  }
  return null;
}

export function mergeAgentDetailWithCatalog(detail: AgentDetail, catalogItem: AgentCatalogItem | undefined): AgentDetail {
  if (!catalogItem) return detail;
  return {
    ...detail,
    id: catalogItem.id,
    displayName: catalogItem.displayName,
    description: catalogItem.description,
    category: catalogItem.category,
    source: catalogItem.source,
    installed: catalogItem.installed,
    connectionState: catalogItem.connectionState,
    ...(catalogItem.enabled !== undefined ? { enabled: catalogItem.enabled } : {}),
    ...(catalogItem.updateAvailable !== undefined ? { updateAvailable: catalogItem.updateAvailable } : {}),
    tags: detail.tags.length > 0 ? detail.tags : catalogItem.tags,
    avatarUrl: detail.avatarUrl || catalogItem.avatarUrl,
  };
}

export function buildCatalogViewModel(
  catalog: AgentCatalogItem[],
  options: {
    scope: CatalogScope;
    category: string;
    query: string;
    page: number;
    pageSize: number;
  },
): CatalogViewModel {
  const query = options.query.trim().toLocaleLowerCase();
  const filtered = catalog.filter(item => {
    if (options.scope === 'mine' && item.source !== 'local' && !item.installed) {
      return false;
    }
    if (options.scope === 'catalog' && options.category && item.category !== options.category) {
      return false;
    }
    if (!query) {
      return true;
    }
    return `${item.displayName} ${item.description} ${item.category}`.toLocaleLowerCase().includes(query);
  });
  const totalPages = Math.max(1, Math.ceil(filtered.length / options.pageSize));
  const page = Math.min(Math.max(options.page, 1), totalPages);
  const start = (page - 1) * options.pageSize;
  return {
    items: filtered.slice(start, start + options.pageSize),
    totalItems: filtered.length,
    page,
    totalPages,
  };
}
