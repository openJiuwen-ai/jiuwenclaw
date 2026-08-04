import { useId, type CSSProperties, type ReactNode } from 'react';

export type FileTypeIconKey =
  | 'pdf'
  | 'docx'
  | 'sheet'
  | 'html'
  | 'json'
  | 'ipynb'
  | 'md'
  | 'text'
  | 'image'
  | 'file'
  | 'unknown';

const ICON_COLORS: Record<FileTypeIconKey, { base: string; light: string; fold: string }> = {
  pdf: { base: '#E24B4A', light: '#F06A5F', fold: '#C53A3A' },
  docx: { base: '#2F6FED', light: '#5B8FF5', fold: '#2459C7' },
  md: { base: '#0D9488', light: '#2DD4BF', fold: '#0F766E' },
  text: { base: '#64748B', light: '#94A3B8', fold: '#475569' },
  sheet: { base: '#1FA971', light: '#3FBF88', fold: '#178A5B' },
  html: { base: '#F08A24', light: '#F5A24D', fold: '#D47312' },
  json: { base: '#6B7C93', light: '#8796A8', fold: '#556578' },
  ipynb: { base: '#7B61FF', light: '#9580FF', fold: '#6349E0' },
  image: { base: '#E45CA8', light: '#F07BC0', fold: '#C9448F' },
  file: { base: '#3B82F6', light: '#60A5FA', fold: '#2563EB' },
  unknown: { base: '#9AA3AF', light: '#B0B8C2', fold: '#7F8894' },
};

function DocumentShell({
  typeKey,
  children,
  size,
  gradientId,
}: {
  typeKey: FileTypeIconKey;
  children?: ReactNode;
  size: number;
  gradientId: string;
}) {
  const colors = ICON_COLORS[typeKey];
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 48 48"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      style={{ display: 'block', flexShrink: 0 } as CSSProperties}
    >
      <defs>
        <linearGradient id={gradientId} x1="24" y1="2" x2="24" y2="46" gradientUnits="userSpaceOnUse">
          <stop stopColor={colors.light} />
          <stop offset="1" stopColor={colors.base} />
        </linearGradient>
      </defs>
      <path
        d="M10 4C8.34315 4 7 5.34315 7 7V41C7 42.6569 8.34315 44 10 44H38C39.6569 44 41 42.6569 41 41V16L31 4H10Z"
        fill={`url(#${gradientId})`}
      />
      <path d="M31 4V12C31 14.2091 32.7909 16 35 16H41L31 4Z" fill={colors.fold} />
      <path d="M31 4L41 16H35C32.7909 16 31 14.2091 31 12V4Z" fill="rgba(255,255,255,0.28)" />
      {children}
    </svg>
  );
}

function GlyphPdf() {
  return (
    <path
      fill="white"
      d="M17.8 31.8c.4-3.8 1.9-7.6 4.4-10.5 2.2-2.5 5-4.1 8.2-3.6 1.7.3 2.9 1.6 2.7 3.2-.3 2.2-2.3 3.4-4.4 4.1-2.6.8-5.3.7-7.9 1.5-2.1.7-3.4 2.2-3 5.3zm4.6-1.8c1.6-1.8 4.4-2.1 6.8-2.7 2-.5 3.5-1.3 3.7-2.8.1-.8-.5-1.4-1.4-1.5-2.1-.3-4.1.8-5.7 2.4-1.8 1.9-3 4.3-3.4 6.8.1-.7.1-1.5 0-2.2z"
    />
  );
}

function GlyphLines() {
  return (
    <>
      <rect x="15" y="20" width="18" height="2.6" rx="1.3" fill="white" />
      <rect x="15" y="25.2" width="14" height="2.6" rx="1.3" fill="white" />
      <rect x="15" y="30.4" width="16" height="2.6" rx="1.3" fill="white" />
    </>
  );
}

/** Markdown: hash headings style */
function GlyphMd() {
  return (
    <>
      <path
        d="M18 19V33M22.5 19V33"
        stroke="white"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <path
        d="M16.5 24.5H24"
        stroke="white"
        strokeWidth="2.4"
        strokeLinecap="round"
      />
      <rect x="27" y="22" width="5.5" height="2.2" rx="1.1" fill="white" />
      <rect x="27" y="27" width="8" height="2.2" rx="1.1" fill="white" />
    </>
  );
}

/** Plain text: letter T */
function GlyphTxt() {
  return (
    <path
      d="M17 20H31M24 20V33"
      stroke="white"
      strokeWidth="2.6"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  );
}

function GlyphSheet() {
  return (
    <>
      <rect x="15" y="19.5" width="5.8" height="5.8" rx="1.1" fill="white" />
      <rect x="22.6" y="19.5" width="5.8" height="5.8" rx="1.1" fill="white" />
      <rect x="30.2" y="19.5" width="2.4" height="5.8" rx="1" fill="white" fillOpacity="0.9" />
      <rect x="15" y="27.2" width="5.8" height="5.8" rx="1.1" fill="white" />
      <rect x="22.6" y="27.2" width="5.8" height="5.8" rx="1.1" fill="white" />
      <rect x="30.2" y="27.2" width="2.4" height="5.8" rx="1" fill="white" fillOpacity="0.9" />
    </>
  );
}

function GlyphPie() {
  return (
    <>
      <circle cx="24" cy="26" r="7.5" fill="white" fillOpacity="0.35" />
      <path d="M24 18.5V26H31.5A7.5 7.5 0 0 0 24 18.5Z" fill="white" />
      <path d="M24 26V33.5A7.5 7.5 0 0 0 30.8 29.2L24 26Z" fill="white" fillOpacity="0.85" />
    </>
  );
}

function GlyphCode() {
  return (
    <path
      d="M20.2 22.2L17 25.2L20.2 28.2M27.8 22.2L31 25.2L27.8 28.2M25.2 20.5L22.8 30"
      stroke="white"
      strokeWidth="2.2"
      strokeLinecap="round"
      strokeLinejoin="round"
    />
  );
}

function GlyphImage() {
  return (
    <>
      <rect x="14.5" y="19" width="19" height="14" rx="2.2" fill="white" fillOpacity="0.95" />
      <circle cx="19.5" cy="23.5" r="1.8" fill="#E45CA8" />
      <path d="M15.5 30.5L21 26L25 29L28.5 25.5L32.5 30.5H15.5Z" fill="#E45CA8" />
    </>
  );
}

function GlyphQuestion() {
  return (
    <text
      x="24"
      y="31.5"
      textAnchor="middle"
      fill="white"
      fontSize="18"
      fontWeight="700"
      fontFamily="Arial, Helvetica, sans-serif"
    >
      ?
    </text>
  );
}

function glyphFor(typeKey: FileTypeIconKey): ReactNode {
  switch (typeKey) {
    case 'pdf':
      return <GlyphPdf />;
    case 'docx':
      return <GlyphLines />;
    case 'md':
      return <GlyphMd />;
    case 'text':
      return <GlyphTxt />;
    case 'sheet':
      return <GlyphSheet />;
    case 'html':
      return <GlyphPie />;
    case 'json':
    case 'ipynb':
      return <GlyphCode />;
    case 'image':
      return <GlyphImage />;
    case 'unknown':
      return <GlyphQuestion />;
    case 'file':
      return null;
    default:
      return <GlyphQuestion />;
  }
}

export function getFileTypeIconKeyFromFilename(filename: string, kind?: string): FileTypeIconKey {
  if (kind === 'image') return 'image';
  const idx = filename.lastIndexOf('.');
  const ext = idx >= 0 ? filename.slice(idx).toLowerCase() : '';
  if (ext === '.pdf') return 'pdf';
  if (ext === '.docx' || ext === '.doc') return 'docx';
  if (ext === '.xlsx' || ext === '.xls' || ext === '.csv' || ext === '.tsv') return 'sheet';
  if (ext === '.html' || ext === '.htm' || ext === '.css') return 'html';
  if (ext === '.json') return 'json';
  if (ext === '.ipynb') return 'ipynb';
  if (ext === '.md' || ext === '.markdown') return 'md';
  if (ext === '.txt') return 'text';
  if (['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.svg'].includes(ext)) return 'image';
  if (!ext) return 'file';
  return 'unknown';
}

export function splitFilenameParts(filename: string): { stem: string; extLabel: string } {
  const idx = filename.lastIndexOf('.');
  if (idx <= 0 || idx === filename.length - 1) {
    return { stem: filename || 'file', extLabel: '' };
  }
  return {
    stem: filename.slice(0, idx),
    extLabel: filename.slice(idx + 1).toLowerCase(),
  };
}

export function FileTypeIcon({
  typeKey,
  size = 32,
}: {
  typeKey: FileTypeIconKey;
  size?: number;
}) {
  const reactId = useId().replace(/:/g, '');
  const key: FileTypeIconKey = ICON_COLORS[typeKey] ? typeKey : 'unknown';
  return (
    <DocumentShell typeKey={key} size={size} gradientId={`fti-${key}-${reactId}`}>
      {glyphFor(key)}
    </DocumentShell>
  );
}
