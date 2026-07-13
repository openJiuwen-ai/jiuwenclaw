import {
  Children,
  isValidElement,
  useEffect,
  useId,
  useMemo,
  useState,
  type AnchorHTMLAttributes,
  type HTMLAttributes,
  type ReactNode,
} from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { MermaidConfig } from 'mermaid';
import type { Element as HastElement } from 'hast';
import './MarkdownRenderer.css';

interface MarkdownRendererProps {
  content: string;
  className?: string;
  testId?: string;
}

type MermaidRenderState =
  | { status: 'loading'; svg: '' }
  | { status: 'rendered'; svg: string }
  | { status: 'error'; svg: '' };

const MERMAID_CONFIG: MermaidConfig = {
  startOnLoad: false,
  suppressErrorRendering: true,
  securityLevel: 'strict',
  htmlLabels: false,
  flowchart: { useMaxWidth: false },
};

function getMermaidTheme(): 'default' | 'dark' {
  return document.documentElement.getAttribute('data-theme') === 'light'
    ? 'default'
    : 'dark';
}

function MermaidBlock({ code }: { code: string }) {
  const diagramId = `mermaid-${useId().replace(/[^A-Za-z0-9_-]/g, '_')}`;
  const [renderState, setRenderState] = useState<MermaidRenderState>({
    status: 'loading',
    svg: '',
  });

  useEffect(() => {
    let cancelled = false;
    async function render(): Promise<void> {
      setRenderState({ status: 'loading', svg: '' });
      try {
        const mermaid = (await import('mermaid')).default;
        mermaid.initialize({ ...MERMAID_CONFIG, theme: getMermaidTheme() });
        const { svg } = await mermaid.render(diagramId, code);
        if (!cancelled) setRenderState({ status: 'rendered', svg });
      } catch {
        if (!cancelled) setRenderState({ status: 'error', svg: '' });
      }
    }
    render();
    return () => { cancelled = true; };
  }, [code, diagramId]);

  if (renderState.status === 'error') {
    return (
      <pre className="mermaid-error" data-mermaid-status="error">
        <code>{code}</code>
      </pre>
    );
  }

  if (renderState.status === 'loading') {
    return (
      <pre className="mermaid-loading" data-mermaid-status="loading">
        <code>{code}</code>
      </pre>
    );
  }

  return (
    <div
      className="mermaid-diagram"
      data-mermaid-status="rendered"
    >
      <div className="mermaid-canvas">
        <div
          className="mermaid-svg-wrapper"
          dangerouslySetInnerHTML={{ __html: renderState.svg }}
        />
      </div>
    </div>
  );
}

function getMermaidCode(children: ReactNode): string | null {
  const childArray = Children.toArray(children);
  if (childArray.length !== 1) {
    return null;
  }

  const child = childArray[0];
  if (!isValidElement<HTMLAttributes<HTMLElement>>(child) || child.type !== 'code') {
    return null;
  }

  const className = child.props.className || '';
  if (!/(^|\s)language-mermaid(\s|$)/.test(className)) {
    return null;
  }

  return String(child.props.children).replace(/\n$/, '');
}

function isCompleteCodeFence(
  contentLines: string[],
  node?: HastElement
): boolean {
  const startLine = node?.position?.start?.line;
  const endLine = node?.position?.end?.line;
  if (!startLine || !endLine) {
    return false;
  }

  const opener = contentLines[startLine - 1];
  const closer = contentLines[endLine - 1];
  if (!opener || !closer) {
    return false;
  }

  const openMatch = /^( {0,3})(`{3,}|~{3,})/.exec(opener);
  if (!openMatch) {
    return false;
  }

  const fenceChar = openMatch[2][0];
  const fenceLen = openMatch[2].length;
  const closePattern = new RegExp(`^ {0,3}\\${fenceChar}{${fenceLen},}\\s*$`);
  return closePattern.test(closer);
}

function MarkdownLink({
  href,
  children,
  ...props
}: AnchorHTMLAttributes<HTMLAnchorElement>) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" {...props}>
      {children}
    </a>
  );
}

function MarkdownPre({
  children,
  node,
  contentLines,
  ...props
}: HTMLAttributes<HTMLPreElement> & {
  node?: HastElement;
  contentLines: string[];
}) {
  const code = getMermaidCode(children);
  if (code !== null && isCompleteCodeFence(contentLines, node)) {
    return <MermaidBlock code={code} />;
  }

  return (
    <pre {...props}>
      {children}
    </pre>
  );
}

export function MarkdownRenderer({
  content,
  className,
  testId,
}: MarkdownRendererProps) {
  const contentLines = useMemo(
    () => content.split(/\r\n|\n|\r/),
    [content]
  );

  const components = useMemo(
    () => ({
      a: MarkdownLink,
      pre: (props: HTMLAttributes<HTMLPreElement> & { node?: HastElement }) => (
        <MarkdownPre {...props} contentLines={contentLines} />
      ),
    }),
    [contentLines]
  );

  return (
    <div className={className} data-testid={testId}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={components}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}
