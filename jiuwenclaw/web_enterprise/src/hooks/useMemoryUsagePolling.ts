import { useEffect, type RefObject } from 'react';
import { webRequest } from '../services/webClient';

const MEMORY_USAGE_POLL_INTERVAL_MS = 30_000;

type SetMemoryUsage = (memoryUsage: {
  rssMb: number | null;
  usedPercent: number | null;
} | null) => void;

export function useMemoryUsagePolling(
  isConnected: boolean,
  setMemoryUsage: SetMemoryUsage,
  containerRef: RefObject<Element | null>
): void {
  useEffect(() => {
    if (!isConnected) {
      setMemoryUsage(null);
      return;
    }

    let disposed = false;
    let timerId: number | null = null;
    let panelVisible = true;

    const refreshMemoryUsage = async () => {
      try {
        const payload = await webRequest<Record<string, unknown>>('memory.compute');
        if (disposed) return;

        const rssMb =
          typeof payload.rss_mb === 'number' && Number.isFinite(payload.rss_mb)
            ? payload.rss_mb
            : null;
        const usedPercent =
          typeof payload.used_percent === 'number' && Number.isFinite(payload.used_percent)
            ? payload.used_percent
            : null;

        setMemoryUsage({ rssMb, usedPercent });
      } catch {
        if (!disposed) {
          setMemoryUsage(null);
        }
      }
    };

    const stopPolling = () => {
      if (timerId != null) {
        window.clearInterval(timerId);
        timerId = null;
      }
    };

    const startPolling = () => {
      if (timerId != null) {
        return;
      }
      void refreshMemoryUsage();
      timerId = window.setInterval(() => {
        void refreshMemoryUsage();
      }, MEMORY_USAGE_POLL_INTERVAL_MS);
    };

    const syncPolling = () => {
      if (disposed) {
        return;
      }
      if (panelVisible && !document.hidden) {
        startPolling();
      } else {
        stopPolling();
      }
    };

    const element = containerRef.current;
    let observer: IntersectionObserver | null = null;
    if (element) {
      observer = new IntersectionObserver(
        (entries) => {
          panelVisible = entries.some((entry) => entry.isIntersecting);
          syncPolling();
        },
        { threshold: 0 }
      );
      observer.observe(element);
    }

    const onVisibilityChange = () => {
      syncPolling();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);

    syncPolling();

    return () => {
      disposed = true;
      stopPolling();
      observer?.disconnect();
      document.removeEventListener('visibilitychange', onVisibilityChange);
    };
  }, [containerRef, isConnected, setMemoryUsage]);
}
