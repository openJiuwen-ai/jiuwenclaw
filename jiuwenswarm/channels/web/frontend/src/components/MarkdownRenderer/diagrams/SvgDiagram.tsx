import { useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { UNTRUSTED_STATIC_PREVIEW_SANDBOX } from '../isolatedPreview';
import { MarkdownRecordIdContext } from '../markdownRecordContext';
import { fetchHistoryRecordContent, pickSvgBlockFor } from '../../../features/historyRecordContent';
import { useChatStore } from '../../../stores/chatStore';
import { DiagramViewer, type DiagramViewMode } from './DiagramViewer';
import { getSvgMarkupStatus, getSvgPreview, isTruncatedMarkup, stripTruncationMarker, SVG_PREVIEW_DOCUMENT, updateSvgPreview, type SvgMarkupStatus } from './svgPreview';

interface SvgDiagramProps {
  code: string;
  complete: boolean;
  isStreaming: boolean;
}

function getStatusText(status: SvgMarkupStatus, translate: (key: string) => string): string | undefined {
  if (status === 'streaming') return translate('svg.streaming');
  if (status === 'truncated') return translate('svg.truncated');
  if (status === 'invalid') return translate('svg.invalid');
  return undefined;
}

export function SvgDiagram({ code, complete, isStreaming }: SvgDiagramProps): JSX.Element {
  const { t } = useTranslation();
  const previewRef = useRef<HTMLIFrameElement>(null);
  const [requestedViewMode, setRequestedViewMode] = useState<DiagramViewMode>('image');
  const truncated = useMemo(() => isTruncatedMarkup(code), [code]);
  const exportSource = useMemo(() => stripTruncationMarker(code), [code]);
  const preview = useMemo(() => getSvgPreview(exportSource), [exportSource]);
  const status = useMemo(
    () => getSvgMarkupStatus(preview, complete, isStreaming, truncated),
    [complete, isStreaming, preview, truncated],
  );
  const imageViewDisabled = preview === null;
  const viewMode: DiagramViewMode = imageViewDisabled ? 'code' : requestedViewMode;
  const canExport = status === 'ready' || status === 'truncated';
  const previewMarkup = preview?.markup ?? exportSource;

  const recordId = useContext(MarkdownRecordIdContext);
  const resolveSourceCode = useMemo(() => {
    if (!truncated || !recordId) return undefined;
    return async (): Promise<string | null> => {
      const sessionId = useChatStore.getState().activeSessionId;
      if (!sessionId) return null;
      const record = await fetchHistoryRecordContent(sessionId, recordId);
      if (!record) return null;
      return pickSvgBlockFor(record.content, exportSource);
    };
  }, [truncated, recordId, exportSource]);

  useEffect(() => {
    if (viewMode === 'image') updateSvgPreview(previewRef.current, previewMarkup);
  }, [previewMarkup, viewMode]);

  return (
    <DiagramViewer
      className="svg-diagram"
      data-svg-status={status}
      viewMode={viewMode}
      onViewModeChange={setRequestedViewMode}
      imageViewDisabled={imageViewDisabled}
      statusText={getStatusText(status, t)}
      statusTone={status === 'invalid' || status === 'truncated' ? 'warning' : 'default'}
      feedbackPosition="start"
      exportConfig={{
        sourceCode: exportSource,
        sourceFilename: 'diagram.svg',
        sourceMimeType: 'image/svg+xml;charset=utf-8',
        renderedSvg: previewMarkup,
        imageFilename: 'diagram.png',
        downloadEnabled: canExport,
        resolveSourceCode,
        ...(truncated
          ? { partialSourceFilename: 'diagram.partial.svg', partialImageFilename: 'diagram.partial.png' }
          : {}),
      }}
    >
      {viewMode === 'image' ? (
        <div className="svg-diagram__canvas" aria-busy={status === 'streaming'}>
          <iframe
            ref={previewRef}
            className="svg-diagram__frame"
            style={preview?.aspectRatio ? { aspectRatio: preview.aspectRatio } : undefined}
            title={t('svg.previewTitle')}
            sandbox={UNTRUSTED_STATIC_PREVIEW_SANDBOX}
            srcDoc={SVG_PREVIEW_DOCUMENT}
            onLoad={() => updateSvgPreview(previewRef.current, previewMarkup)}
          />
        </div>
      ) : (
        <div className="svg-diagram__code-view">
          <pre>
            <code>{code}</code>
          </pre>
        </div>
      )}
    </DiagramViewer>
  );
}
