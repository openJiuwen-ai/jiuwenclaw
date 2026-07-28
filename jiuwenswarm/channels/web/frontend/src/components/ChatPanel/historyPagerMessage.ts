export interface HistoryPagerProgress {
  loadedPages: number;
  loadingMore: boolean;
}

export interface HistoryPagerMessageState {
  loadingMore: boolean;
  /** 本轮 load more 开始时的 loadedPages，用于判断这一轮是否真的翻到了新页 */
  pagesAtLoadStart: number;
  showLoadedMessage: boolean;
}

export function initHistoryPagerMessageState(
  progress: HistoryPagerProgress,
): HistoryPagerMessageState {
  return {
    loadingMore: progress.loadingMore,
    pagesAtLoadStart: progress.loadedPages,
    showLoadedMessage: false,
  };
}

/**
 * 「已加载 X / Y 页」只在一次 load more 真正完成后提示：
 * loadingMore 由 true 变为 false，且 loadedPages 比该轮开始时更大。
 * 首屏渲染与后台预取（loadingMore 保持 false）都不应触发提示。
 */
export function reduceHistoryPagerMessage(
  prev: HistoryPagerMessageState,
  progress: HistoryPagerProgress,
): HistoryPagerMessageState {
  if (progress.loadingMore) {
    const pagesAtLoadStart = prev.loadingMore ? prev.pagesAtLoadStart : progress.loadedPages;
    if (prev.loadingMore && pagesAtLoadStart === prev.pagesAtLoadStart && !prev.showLoadedMessage) {
      return prev;
    }
    return { loadingMore: true, pagesAtLoadStart, showLoadedMessage: false };
  }

  const finishedLoad = prev.loadingMore && progress.loadedPages > prev.pagesAtLoadStart;
  if (!finishedLoad && !prev.loadingMore) {
    return prev;
  }

  return {
    loadingMore: false,
    pagesAtLoadStart: progress.loadedPages,
    showLoadedMessage: finishedLoad,
  };
}

export function dismissHistoryPagerMessage(
  prev: HistoryPagerMessageState,
): HistoryPagerMessageState {
  if (!prev.showLoadedMessage) return prev;
  return { ...prev, showLoadedMessage: false };
}
