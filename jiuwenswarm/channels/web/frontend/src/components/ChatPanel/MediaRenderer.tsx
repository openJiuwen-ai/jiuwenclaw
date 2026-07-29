import { MediaItem } from '../../types';

interface MediaRendererProps {
  items: MediaItem[];
}

export function MediaRenderer({ items }: MediaRendererProps) {
  if (!items.length) {
    return null;
  }

  return (
    <div className="mt-2 space-y-2">
      {items.map((item, index) => (
        <MediaItemView key={`${item.filename}-${index}`} item={item} />
      ))}
    </div>
  );
}

function MediaItemView({ item }: { item: MediaItem }) {
  const mimeType = item.mimeType || item.mime_type || 'application/octet-stream';
  const base64Data = item.base64Data || item.base64_data;
  const src = base64Data
    ? `data:${mimeType};base64,${base64Data}`
    : item.url || (item.path ? `/file-api/raw-file?path=${encodeURIComponent(item.path)}` : undefined);

  if (!src) {
    return null;
  }

  switch (item.type) {
    case 'image':
      return (
        <img
          src={src}
          alt={item.filename}
          className="max-w-full rounded-lg border border-border"
        />
      );
    case 'audio':
      return (
        <audio controls className="w-full">
          <source src={src} type={mimeType} />
        </audio>
      );
    case 'video':
      return (
        <video controls className="max-w-full rounded-lg border border-border">
          <source src={src} type={mimeType} />
        </video>
      );
    case 'document':
    default:
      return (
        <a
          href={src}
          download={item.filename}
          className="inline-flex items-center gap-2 rounded-md border border-border bg-secondary px-3 py-2 text-sm text-text-strong hover:bg-secondary/80"
        >
          <span className="truncate max-w-[240px]">{item.filename}</span>
        </a>
      );
  }
}
