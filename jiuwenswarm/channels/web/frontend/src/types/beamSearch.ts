export type BeamNodeStatus = 'seed' | 'pending' | 'selected' | 'rejected' | 'final';

export interface BeamSearchNode {
  id: string;
  label: string;
  status: BeamNodeStatus;
}

export interface BeamSearchEdge {
  source: string;
  target: string;
  status: BeamNodeStatus;
}

export interface BeamSearchGraph {
  nodes: BeamSearchNode[];
  edges: BeamSearchEdge[];
}

export interface BeamSearchProgress {
  event: string;
  language: 'cn' | 'en';
  roundIndex: number;
  graph: BeamSearchGraph;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object'
    ? value as Record<string, unknown>
    : null;
}

export function parseBeamSearchProgress(raw: unknown): BeamSearchProgress | undefined {
  const record = asRecord(raw);
  if (!record) return undefined;
  const event = asRecord(record.beam_search_event) ?? record;
  const graph = asRecord(event.graph);
  if (!graph || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) {
    return undefined;
  }
  return {
    event: typeof event.event === 'string' ? event.event : '',
    language: event.language === 'en' ? 'en' : 'cn',
    roundIndex: typeof event.round_index === 'number' ? event.round_index : 0,
    graph: {
      nodes: graph.nodes as BeamSearchNode[],
      edges: graph.edges as BeamSearchEdge[],
    },
  };
}
