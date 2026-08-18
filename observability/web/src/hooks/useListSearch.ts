import { useState } from 'react';
import { useDebouncedValue } from './useDebouncedValue';

/** 列表页搜索：输入态 + 防抖后的 query（空串时为 undefined，便于传给 API）。 */
export function useListSearch(debounceMs = 700) {
  const [searchInput, setSearchInput] = useState('');
  const debouncedSearch = useDebouncedValue(searchInput, debounceMs);
  const searchQuery = debouncedSearch.trim() || undefined;
  return { searchInput, setSearchInput, searchQuery };
}
