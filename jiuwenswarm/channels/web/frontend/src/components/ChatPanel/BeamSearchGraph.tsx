import { useMemo, useState } from 'react';
import clsx from 'clsx';
import { ChevronDown } from 'lucide-react';
import type { BeamSearchProgress } from '../../types/beamSearch';
import { MarkdownRenderer } from '../MarkdownRenderer';
import './BeamSearchGraph.css';

function escapeLabel(value: string): string {
  return value.replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

function nodeKey(id: string, index: number): string {
  return `beam_${index}_${id.replace(/[^A-Za-z0-9_]/g, '_')}`;
}

function buildMermaid(progress: BeamSearchProgress): string {
  const lines = ['flowchart LR'];
  const keys = new Map<string, string>();
  progress.graph.nodes.forEach((node, index) => {
    const key = nodeKey(node.id, index);
    keys.set(node.id, key);
    lines.push(`  ${key}["${escapeLabel(node.label || node.id)}"]`);
  });
  progress.graph.edges.forEach((edge) => {
    const source = keys.get(edge.source);
    const target = keys.get(edge.target);
    if (source && target) lines.push(`  ${source} --> ${target}`);
  });
  progress.graph.nodes.forEach((node, index) => {
    lines.push(`  class ${nodeKey(node.id, index)} ${node.status}`);
  });
  lines.push('  classDef seed fill:#eff6ff,stroke:#2563eb,color:#1e3a8a');
  lines.push('  classDef pending fill:#fff,stroke:#60a5fa,color:#1f2937');
  lines.push('  classDef selected fill:#ecfdf5,stroke:#10b981,color:#065f46');
  lines.push('  classDef rejected fill:#f3f4f6,stroke:#9ca3af,color:#9ca3af');
  lines.push('  classDef final fill:#f5f3ff,stroke:#7c3aed,color:#5b21b6');
  return `\`\`\`mermaid\n${lines.join('\n')}\n\`\`\``;
}

export function BeamSearchGraph({ progress }: { progress: BeamSearchProgress }) {
  const [collapsed, setCollapsed] = useState(false);
  const markdown = useMemo(() => buildMermaid(progress), [progress]);
  const selected = progress.graph.nodes.filter(
    (node) => node.status === 'selected' || node.status === 'final'
  ).length;
  const isEnglish = progress.language === 'en';
  const title = isEnglish ? 'Skill orchestration search' : '技能编排搜索';
  const round = isEnglish
    ? `Round ${progress.roundIndex}`
    : `第 ${progress.roundIndex} 轮`;
  const summary = isEnglish
    ? `${progress.graph.nodes.length} skills · ${selected} selected`
    : `${progress.graph.nodes.length} 个技能 · ${selected} 个入选`;

  return (
    <div className="beam-search animate-rise" data-testid="beam-search-graph">
      <button
        type="button"
        className="beam-search__header"
        onClick={() => setCollapsed((value) => !value)}
        aria-expanded={!collapsed}
      >
        <span className="beam-search__title">
          <span className="beam-search__badge">{title}</span>
          <span>{round}</span>
        </span>
        <span className="beam-search__meta">
          {summary}
          <ChevronDown
            className={clsx('beam-search__chevron', !collapsed && 'is-open')}
            size={14}
            aria-hidden="true"
          />
        </span>
      </button>
      {!collapsed && (
        <div className="beam-search__body">
          <MarkdownRenderer content={markdown} className="beam-search__diagram" />
        </div>
      )}
    </div>
  );
}
