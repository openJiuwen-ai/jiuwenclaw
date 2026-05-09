/**
 * SkillDevFileBrowser - 文件浏览器组件
 */

import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import type { FileTreeNode } from '../../types/skilldev';

interface SkillDevFileBrowserProps {
  fileTree: FileTreeNode[];
  currentFile: { path: string; content: string } | null;
  onFileSelect: (path: string) => void;
}

export function SkillDevFileBrowser({
  fileTree,
  currentFile,
  onFileSelect,
}: SkillDevFileBrowserProps) {
  const { t } = useTranslation();
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(new Set());

  const toggleDir = (path: string) => {
    const newExpanded = new Set(expandedDirs);
    if (newExpanded.has(path)) {
      newExpanded.delete(path);
    } else {
      newExpanded.add(path);
    }
    setExpandedDirs(newExpanded);
  };

  const renderFileTree = (nodes: FileTreeNode[], level = 0) => {
    return nodes.map((node) => {
      const isDir = node.type === 'dir';
      const isExpanded = expandedDirs.has(node.path);
      const paddingLeft = level * 16 + 8;

      if (isDir) {
        return (
          <div key={node.path}>
            <button
              onClick={() => toggleDir(node.path)}
              className="w-full flex items-center gap-2 px-2 py-1.5 hover:bg-hover text-left text-sm"
              style={{ paddingLeft }}
            >
              <svg
                className={`w-4 h-4 text-text-muted transition-transform ${
                  isExpanded ? 'rotate-90' : ''
                }`}
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              <svg className="w-4 h-4 text-warning" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"
                />
              </svg>
              <span className="text-text truncate">{node.path.replace(/\/$/, '').split('/').pop()}</span>
            </button>
            {isExpanded && node.children && (
              <div>{renderFileTree(node.children, level + 1)}</div>
            )}
          </div>
        );
      }

      return (
        <button
          key={node.path}
          onClick={() => onFileSelect(node.path)}
          className={`w-full flex items-center gap-2 px-2 py-1.5 hover:bg-hover text-left text-sm ${
            currentFile?.path === node.path ? 'bg-primary/10 text-primary' : ''
          }`}
          style={{ paddingLeft }}
        >
          <span className="w-4" />
          <svg className="w-4 h-4 text-text-muted" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
            />
          </svg>
          <span className="truncate">{node.path.split('/').pop()}</span>
          {node.size !== undefined && (
            <span className="text-xs text-text-muted ml-auto">{formatSize(node.size)}</span>
          )}
        </button>
      );
    });
  };

  const formatSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  };

  return (
    <div className="flex h-full">
      {/* 文件树 */}
      <div className="w-64 border-r border-border bg-secondary overflow-y-auto">
        <div className="p-3 border-b border-border">
          <h3 className="text-sm font-medium text-text">{t('skilldev.fileTree')}</h3>
          <p className="text-xs text-text-muted mt-1 truncate">
            {t('skilldev.skillDirOnly', '仅展示 skill 目录')}
          </p>
        </div>
        {fileTree.length === 0 ? (
          <p className="p-4 text-sm text-text-muted italic">{t('skilldev.noFiles')}</p>
        ) : (
          <div className="py-2">{renderFileTree(fileTree)}</div>
        )}
      </div>

      {/* 文件内容 */}
      <div className="flex-1 bg-bg overflow-auto">
        {currentFile ? (
          <div className="h-full flex flex-col">
            <div className="px-4 py-2 border-b border-border bg-secondary">
              <p className="text-sm font-medium text-text">{currentFile.path}</p>
            </div>
            <pre className="flex-1 p-4 text-sm font-mono text-text overflow-auto whitespace-pre-wrap">
              {currentFile.content}
            </pre>
          </div>
        ) : (
          <div className="h-full flex items-center justify-center text-text-muted">
            <p>{t('skilldev.selectFile')}</p>
          </div>
        )}
      </div>
    </div>
  );
}
