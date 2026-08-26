export interface ChatContextItem {
  id: number;
  role: 'user' | 'assistant' | 'tool';
  text: string;
}

export interface SearchJobPayload {
  job_id?: string;
  search_session_id?: string;
  question?: string;
  query?: string;
  result?: string;
  error?: string;
  engine?: string;
  status?: 'running' | 'completed' | 'failed';
  latency_ms?: number;
  progress?: SearchProgressEntry;
  progress_history?: SearchProgressEntry[];
}

export interface SearchProgressEntry {
  stage: string;
  title: string;
  detail?: string;
  status: 'running' | 'completed' | 'failed';
  sequence: number;
  elapsed_ms?: number;
  tool_name?: string;
  tool_call_id?: string;
}

export interface SearchProgressJob {
  id: string;
  query: string;
  status: 'running' | 'completed' | 'failed';
  latencyMs?: number;
  progress: SearchProgressEntry[];
}

export interface SearchJobState {
  id: string;
  searchSessionId: string;
  question: string;
  query: string;
  status: 'running' | 'queued' | 'failed';
  frameDataUrl?: string;
}

export interface AgentAction {
  answer?: string;
  current_task?: string;
  tools_used?: string[];
  search_job?: {
    id?: string;
    question?: string;
    query?: string;
    status?: string;
    search_session_id?: string;
  } | null;
}

export interface VideoSessionConfig {
  provider?: 'realtime' | 'joyai';
  url?: string;
  model: string;
  ref_audio_base64?: string;
}

export interface JoyAIFrameResult extends AgentAction {
  decision?: 'silence' | 'response' | 'delegation';
  response?: string;
  delegation?: string;
  joyai_session_id?: string;
  latency_ms?: number;
}

export interface TtsStreamPayload {
  stream_id?: string;
  audio_base64?: string;
  sample_rate?: number;
  error?: string;
  first_chunk_ms?: number;
}
