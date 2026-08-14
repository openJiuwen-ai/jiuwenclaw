import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { convertSvgToPng, saveBlob } from './diagramExport';

export interface DiagramExportConfig {
  sourceCode: string;
  sourceFilename: string;
  sourceMimeType: string;
  renderedSvg: string;
  imageFilename: string;
  downloadEnabled?: boolean;
  resolveSourceCode?: () => Promise<string | null>;
  partialSourceFilename?: string;
  partialImageFilename?: string;
}

interface DiagramExportActions {
  feedback: string | null;
  copyCode: () => Promise<void>;
  downloadSource: () => Promise<void>;
  downloadImage: () => Promise<void>;
}

export function useDiagramExportActions(config: DiagramExportConfig): DiagramExportActions {
  const { t } = useTranslation();
  const [feedback, setFeedback] = useState<string | null>(null);

  useEffect(() => {
    if (!feedback) return;
    const timeout = window.setTimeout(() => setFeedback(null), 2400);
    return () => window.clearTimeout(timeout);
  }, [feedback]);

  async function resolveFullSource(): Promise<string | null> {
    if (!config.resolveSourceCode) return null;
    setFeedback(t('diagram.restoringSource'));
    try {
      return await config.resolveSourceCode();
    } catch {
      return null;
    }
  }

  async function copyCode(): Promise<void> {
    const source = (await resolveFullSource()) ?? config.sourceCode;
    try {
      await navigator.clipboard.writeText(source);
      setFeedback(t('diagram.copied'));
    } catch {
      setFeedback(t('diagram.copyFailed'));
    }
  }

  async function downloadSource(): Promise<void> {
    const full = await resolveFullSource();
    const filename = full === null && config.partialSourceFilename
      ? config.partialSourceFilename
      : config.sourceFilename;
    const source = new Blob([full ?? config.sourceCode], { type: config.sourceMimeType });
    try {
      const outcome = await saveBlob(source, filename);
      setFeedback(outcome === 'failed' ? t('diagram.downloadSourceFailed') : null);
    } catch {
      setFeedback(t('diagram.downloadSourceFailed'));
    }
  }

  async function downloadImage(): Promise<void> {
    const full = await resolveFullSource();
    const filename = full === null && config.partialImageFilename
      ? config.partialImageFilename
      : config.imageFilename;
    setFeedback(t('diagram.preparingImage'));
    try {
      const image = await convertSvgToPng(full ?? config.renderedSvg);
      const outcome = await saveBlob(image, filename);
      setFeedback(outcome === 'failed' ? t('diagram.downloadImageFailed') : null);
    } catch {
      setFeedback(t('diagram.downloadImageFailed'));
    }
  }

  return { feedback, copyCode, downloadSource, downloadImage };
}
