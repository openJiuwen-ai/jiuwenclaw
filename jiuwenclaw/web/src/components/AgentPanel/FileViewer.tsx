import { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';

interface FileViewerProps {
  filePath: string;
  fileName: string;
}

export function FileViewer({ filePath, fileName }: FileViewerProps) {
  const { t } = useTranslation();
  const [content, setContent] = useState<string>('');
  const [draftContent, setDraftContent] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [isEditing, setIsEditing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);
  const isMarkdown = fileName.toLowerCase().endsWith('.md') || fileName.toLowerCase().endsWith('.mdx');
  const fileNotFound = Boolean(error && error.includes('HTTP 404'));

  useEffect(() => {
    if (!filePath) return;
    if (!isMarkdown) {
      setLoading(false);
      setError(null);
      setSaveError(null);
      setIsEditing(false);
      setSaving(false);
      setContent('');
      setDraftContent('');
      return;
    }

    const loadFile = async () => {
      setLoading(true);
      setError(null);
      setSaveError(null);
      setIsEditing(false);
      setSaving(false);
      
      try {
        const encodedPath = encodeURIComponent(filePath);
        const url = `/file-api/file-content?path=${encodedPath}`;
        const response = await fetch(url);
        
        if (!response.ok) {
          const errorData = await response.text();
          throw new Error(`HTTP ${response.status}: ${errorData.substring(0, 100)}`);
        }
        
        const text = await response.text();
        setContent(text);
        setDraftContent(text);
      } catch (err) {
        console.error('Failed to load file:', err);
        setError(err instanceof Error ? err.message : t('fileViewer.unknownError'));
      } finally {
        setLoading(false);
      }
    };

    loadFile();
  }, [filePath, fileName, isMarkdown]);

  const handleStartEdit = () => {
    setDraftContent(content);
    setSaveError(null);
    setIsEditing(true);
  };

  const handleCancelEdit = () => {
    setDraftContent(content);
    setSaveError(null);
    setIsEditing(false);
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const response = await fetch('/file-api/file-content', {
        method: 'POST',
        headers: {
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          path: filePath,
          content: draftContent,
        }),
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`HTTP ${response.status}: ${errorText.substring(0, 120)}`);
      }

      setContent(draftContent);
      setIsEditing(false);
    } catch (err) {
      console.error('Failed to save file:', err);
      setSaveError(err instanceof Error ? err.message : t('fileViewer.unknownError'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="h-full min-h-0 flex flex-col overflow-hidden">
      <div className="flex-shrink-0 px-4 py-3 bg-secondary/30 border-b border-border">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <span className="h-9 w-9 rounded-lg border border-border bg-card flex items-center justify-center text-text-muted flex-shrink-0">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.6} className="h-7 w-7">
                <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
              </svg>
            </span>
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-medium text-text truncate">{fileName}</h3>
              <p className="text-xs text-text-muted mono truncate mt-1" title={filePath}>
                {filePath}
              </p>
            </div>
          </div>
          {isMarkdown && !loading ? (
            <div className="flex items-center gap-2 flex-shrink-0">
              {isEditing ? (
                <>
                  <button
                    type="button"
                    className="btn !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                    onClick={handleCancelEdit}
                    disabled={saving}
                  >
                    {t('common.cancel')}
                  </button>
                  <button
                    type="button"
                    className="btn primary !px-3 !py-1.5 disabled:opacity-50 disabled:cursor-not-allowed"
                    onClick={handleSave}
                    disabled={saving}
                  >
                    {saving ? t('common.saving') : t('common.save')}
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="btn !px-3 !py-1.5"
                  onClick={handleStartEdit}
                >
                  {t('fileViewer.edit')}
                </button>
              )}
            </div>
          ) : null}
        </div>
        {error ? (
          <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-xs text-danger">
            {error}
          </div>
        ) : null}
        {fileNotFound ? (
          <div className="mt-2 rounded-md border border-warning/30 bg-warning/10 px-2.5 py-1.5 text-xs text-warning">
            {t('fileViewer.fileMissingPrefix')} <span className="mono">{filePath}</span> {t('fileViewer.fileMissingSuffix')}
          </div>
        ) : null}
        {saveError ? (
          <div className="mt-2 rounded-md border border-danger/30 bg-danger/10 px-2.5 py-1.5 text-xs text-danger">
            {saveError}
          </div>
        ) : null}
      </div>

      <div className="flex-1 min-h-0 overflow-auto p-5">
        {loading ? (
          <div className="h-full flex items-center justify-center">
            <div className="w-7 h-7 rounded-full border-4 border-border border-t-accent animate-spin" />
          </div>
        ) : isMarkdown ? (
          isEditing ? (
            <textarea
              className="w-full h-full min-h-[280px] resize-none rounded-lg border border-border bg-card p-3 text-sm text-text outline-none focus:border-accent/50"
              value={draftContent}
              onChange={(event) => setDraftContent(event.target.value)}
              disabled={saving}
            />
          ) : (
            <article className="chat-text max-w-none">
              <ReactMarkdown>{content || ' '}</ReactMarkdown>
            </article>
          )
        ) : (
          <div className="h-full flex items-center justify-center text-text-muted text-sm">
            {t('fileViewer.notPreviewable')}
          </div>
        )}
      </div>
    </div>
  );
}
