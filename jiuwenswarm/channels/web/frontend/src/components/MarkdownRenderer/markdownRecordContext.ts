import { createContext } from 'react';

/**
 * History record id for the message currently being rendered, or null when the
 * markdown has no stored record behind it (live preview, artifact panel, …).
 *
 * Lives in its own module so a nested renderer can read it without importing
 * MarkdownRenderer, which would close an import cycle through the fenced-code
 * registry.
 */
export const MarkdownRecordIdContext = createContext<string | null>(null);
