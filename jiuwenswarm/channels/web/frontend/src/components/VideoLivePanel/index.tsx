import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { Camera, FileVideo, LoaderCircle, Mic, Monitor, Send, Square, Video, X } from 'lucide-react';
import { webClient, webRequest } from '../../services/webClient';
import { canPlayJoyAIResponse, JoyAIVoiceSession } from '../../utils/joyaiVoice';
import { RealtimeDuplexSession } from '../../utils/realtimeDuplex';
import { assistantSpeechText, groundedSearchAnswer } from '../../utils/searchPresentation';
import {
  advanceMeaningfulVideoAgentVersion,
  collectVideoAgentTurns,
  VideoAgentSegment,
} from '../../utils/videoAgentSegments';
import './VideoLivePanel.css';

type VideoSource = 'camera' | 'file' | 'screen' | null;
interface CapturedFrame {
  data_url: string;
  source_id: string;
  source_label: string;
}

interface ScreenSource {
  id: string;
  name: string;
  stream: MediaStream;
}

interface ChatContextItem {
  id: number;
  role: 'user' | 'assistant' | 'tool';
  text: string;
}

interface SearchJobPayload {
  job_id?: string;
  search_session_id?: string;
  question?: string;
  query?: string;
  result?: string;
  error?: string;
  engine?: string;
  status?: 'running' | 'completed' | 'failed';
}

interface SearchJobState {
  id: string;
  searchSessionId: string;
  question: string;
  query: string;
  status: 'running' | 'queued' | 'failed';
  frameDataUrl?: string;
}

interface AgentAction {
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

interface VideoSessionConfig {
  provider?: 'realtime' | 'joyai';
  url?: string;
  model: string;
  ref_audio_base64?: string;
}

interface JoyAIFrameResult extends AgentAction {
  decision?: 'silence' | 'response' | 'delegation';
  response?: string;
  delegation?: string;
  joyai_session_id?: string;
  latency_ms?: number;
}

interface TtsStreamPayload {
  stream_id?: string;
  audio_base64?: string;
  sample_rate?: number;
  error?: string;
  first_chunk_ms?: number;
}

const FRAME_INTERVAL_MS = 500;
const MAX_FRAMES = 6;
const MAX_SCREENS = 4;
const MAX_FRAME_WIDTH = 1024;
const SCREEN_PREVIEW_FRAME_RATE = 30;
const FRAME_JPEG_QUALITY = 0.8;
const AGENT_DEBOUNCE_MS = 700;
const FIRST_FRAME_WAIT_MS = 3_000;
const JOYAI_MONITOR_INTERVAL_MS = 1_000;
const JOYAI_MONITOR_CLIENT_BUILD = 'joyai-official-prompt-lifecycle-v8';

function cleanAssistantText(text: string): string {
  return text
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<\/?think>/gi, '')
    .trim();
}

function searchSummary(text: string): string {
  const firstLine = text.split('\n', 1)[0];
  const engine = firstLine.startsWith('Free search results (') && firstLine.includes(') for:')
    ? firstLine.slice('Free search results ('.length).split(') for:', 1)[0]
    : '';
  return `${engine || '免费搜索'}搜索完成`;
}

export function VideoLivePanel() {
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const chatHistoryRef = useRef<HTMLDivElement>(null);
  const cameraStreamRef = useRef<MediaStream | null>(null);
  const screenStreamsRef = useRef<Map<string, MediaStream>>(new Map());
  const screenVideoRefs = useRef<Map<string, HTMLVideoElement>>(new Map());
  const fileUrlRef = useRef<string | null>(null);
  const framesRef = useRef<CapturedFrame[]>([]);
  const duplexRef = useRef<RealtimeDuplexSession | null>(null);
  const joyaiVoiceRef = useRef<JoyAIVoiceSession | null>(null);
  const joyaiActiveRef = useRef(false);
  const joyaiSessionIdRef = useRef('');
  const joyaiMonitorTimerRef = useRef<number | null>(null);
  const joyaiRequestInFlightRef = useRef(false);
  const joyaiRequestQueueRef = useRef<Promise<void>>(Promise.resolve());
  const joyaiQueuedRequestCountRef = useRef(0);
  const joyaiSessionStartedAtRef = useRef(0);
  const joyaiLastFrameTimeRef = useRef(0);
  const answerRef = useRef('');
  const currentTaskRef = useRef('');
  const recentChatRef = useRef<ChatContextItem[]>([]);
  const startingRealtimeRef = useRef<Promise<void> | null>(null);
  const handledAgentTurnsRef = useRef<Set<string>>(new Set());
  const agentSegmentsRef = useRef<VideoAgentSegment[]>([]);
  const agentSegmentOrderRef = useRef(0);
  const agentDebounceTimerRef = useRef<number | null>(null);
  const agentRequestVersionRef = useRef(0);
  const latestMeaningfulAgentVersionRef = useRef(0);
  const chatSequenceRef = useRef(0);
  const pendingTranscriptionsRef = useRef(0);
  const streamingAnswerRef = useRef('');
  const streamingToolJobIdRef = useRef<string | undefined>(undefined);
  const streamingAnswerTurnIdRef = useRef<string | undefined>(undefined);
  const deferredAssistantAnswersRef = useRef<Array<{ text: string; toolJobId?: string }>>([]);
  const assistantAnswersByTurnRef = useRef<Map<string, string>>(new Map());
  const searchSessionRef = useRef('');
  const searchJobsRef = useRef<Map<string, SearchJobState>>(new Map());
  const pollingSearchJobsRef = useRef<Set<string>>(new Set());
  const acceptedSearchJobIdsRef = useRef<Set<string>>(new Set());
  const joyaiTtsGenerationRef = useRef(0);
  const joyaiUserSpeechActiveRef = useRef(false);
  const joyaiUserSpeechEpochRef = useRef(0);
  const joyaiTtsQueueRef = useRef<Promise<void>>(Promise.resolve());
  const joyaiActiveTtsStreamRef = useRef('');
  const joyaiSearchDeliveryQueueRef = useRef<Promise<void>>(Promise.resolve());

  const [source, setSource] = useState<VideoSource>(null);
  const [sourceName, setSourceName] = useState('');
  const [screens, setScreens] = useState<ScreenSource[]>([]);
  const [isPlaying, setIsPlaying] = useState(false);
  const [frameCount, setFrameCount] = useState(0);
  const [question, setQuestion] = useState('');
  const [answer, setAnswer] = useState('');
  const [chatHistory, setChatHistory] = useState<ChatContextItem[]>([]);
  const [streamingAnswer, setStreamingAnswer] = useState('');
  const [error, setError] = useState('');
  const [toolStatus, setToolStatus] = useState('');
  const [model, setModel] = useState('视频模型');
  const [isRecording, setIsRecording] = useState(false);
  const [isAwaitingVoiceTranscript, setIsAwaitingVoiceTranscript] = useState(false);
  const [realtimeStatus, setRealtimeStatus] = useState('');
  const [isRealtimeStarting, setIsRealtimeStarting] = useState(false);
  const [currentTask, setCurrentTask] = useState('');

  const reportRealtimeEvent = (event: string, details: Record<string, unknown> = {}) => {
    void webRequest('video.realtime.telemetry', {
      event,
      client_time: new Date().toISOString(),
      ...details,
    }, { timeoutMs: 5_000 }).catch(() => undefined);
  };

  const waitForFirstFrame = async (): Promise<boolean> => {
    const deadline = Date.now() + FIRST_FRAME_WAIT_MS;
    while (framesRef.current.length === 0 && Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 100));
    }
    return framesRef.current.length > 0;
  };

  const stopModelTransport = () => {
    duplexRef.current?.stop();
    duplexRef.current = null;
    joyaiTtsGenerationRef.current += 1;
    joyaiUserSpeechActiveRef.current = false;
    joyaiUserSpeechEpochRef.current += 1;
    const activeTtsStream = joyaiActiveTtsStreamRef.current;
    joyaiActiveTtsStreamRef.current = '';
    if (activeTtsStream) {
      void webRequest('tts.stream.cancel', { stream_id: activeTtsStream }, { timeoutMs: 5_000 })
        .catch(() => undefined);
    }
    joyaiVoiceRef.current?.stop();
    joyaiVoiceRef.current = null;
    joyaiTtsQueueRef.current = Promise.resolve();
    joyaiSearchDeliveryQueueRef.current = Promise.resolve();
    joyaiActiveRef.current = false;
    joyaiSessionIdRef.current = '';
    joyaiRequestInFlightRef.current = false;
    joyaiSessionStartedAtRef.current = 0;
    joyaiLastFrameTimeRef.current = 0;
    if (joyaiMonitorTimerRef.current !== null) {
      window.clearInterval(joyaiMonitorTimerRef.current);
      joyaiMonitorTimerRef.current = null;
    }
  };

  const resetVisualContext = () => {
    answerRef.current = '';
    handledAgentTurnsRef.current.clear();
    agentSegmentsRef.current = [];
    agentRequestVersionRef.current += 1;
    latestMeaningfulAgentVersionRef.current = agentRequestVersionRef.current;
    chatSequenceRef.current = 0;
    pendingTranscriptionsRef.current = 0;
    streamingAnswerRef.current = '';
    streamingToolJobIdRef.current = undefined;
    streamingAnswerTurnIdRef.current = undefined;
    deferredAssistantAnswersRef.current = [];
    assistantAnswersByTurnRef.current.clear();
    searchSessionRef.current = '';
    searchJobsRef.current.clear();
    pollingSearchJobsRef.current.clear();
    acceptedSearchJobIdsRef.current.clear();
    currentTaskRef.current = '';
    recentChatRef.current = [];
    if (agentDebounceTimerRef.current !== null) window.clearTimeout(agentDebounceTimerRef.current);
    agentDebounceTimerRef.current = null;
    setAnswer('');
    setChatHistory([]);
    setStreamingAnswer('');
    setCurrentTask('');
    setToolStatus('');
  };

  const appendChat = (role: ChatContextItem['role'], text: string) => {
    const normalized = text.trim();
    if (!normalized) return;
    const previous = recentChatRef.current.at(-1);
    if (previous?.role === role && previous.text === normalized) return;
    recentChatRef.current = [
      ...recentChatRef.current,
      { id: ++chatSequenceRef.current, role, text: normalized },
    ].slice(-12);
    setChatHistory(recentChatRef.current);
  };

  const interruptJoyAITts = () => {
    joyaiTtsGenerationRef.current += 1;
    const activeTtsStream = joyaiActiveTtsStreamRef.current;
    joyaiActiveTtsStreamRef.current = '';
    joyaiVoiceRef.current?.interruptPlayback();
    joyaiTtsQueueRef.current = Promise.resolve();
    if (activeTtsStream) {
      void webRequest('tts.stream.cancel', { stream_id: activeTtsStream }, { timeoutMs: 5_000 })
        .catch(() => undefined);
    }
  };

  const speakJoyAIText = (text: string, generation: number) => {
    const voice = joyaiVoiceRef.current;
    const spokenText = assistantSpeechText(text);
    if (
      !joyaiActiveRef.current
      || !voice
      || !spokenText
      || !canPlayJoyAIResponse(
        generation,
        joyaiTtsGenerationRef.current,
        joyaiUserSpeechActiveRef.current,
      )
    ) return;
    const play = async () => {
      if (
        !canPlayJoyAIResponse(
          generation,
          joyaiTtsGenerationRef.current,
          joyaiUserSpeechActiveRef.current,
        )
      ) return;
      const streamId = typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `tts-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      let receivedStreamAudio = false;
      let streamStarted = false;
      const playback = voice.beginPcmStream(streamId);
      joyaiActiveTtsStreamRef.current = streamId;
      const unsubscribeChunk = webClient.on<TtsStreamPayload>('video.tts.chunk', ({ payload }) => {
        if (
          payload.stream_id !== streamId
          || !canPlayJoyAIResponse(
            generation,
            joyaiTtsGenerationRef.current,
            joyaiUserSpeechActiveRef.current,
          )
        ) return;
        const audioBase64 = payload.audio_base64 || '';
        if (!audioBase64) return;
        receivedStreamAudio = true;
        try {
          voice.appendPcm16Chunk(streamId, audioBase64, payload.sample_rate || 24_000);
        } catch (streamError) {
          voice.failPcmStream(
            streamId,
            streamError instanceof Error ? streamError.message : 'JoyAI 音频分块解析失败',
          );
        }
      });
      const unsubscribeDone = webClient.on<TtsStreamPayload>('video.tts.done', ({ payload }) => {
        if (payload.stream_id !== streamId) return;
        voice.finishPcmStream(streamId);
      });
      const unsubscribeCancelled = webClient.on<TtsStreamPayload>(
        'video.tts.cancelled',
        ({ payload }) => {
          if (payload.stream_id === streamId) voice.interruptPlayback();
        },
      );
      const unsubscribeError = webClient.on<TtsStreamPayload>('video.tts.error', ({ payload }) => {
        if (payload.stream_id !== streamId) return;
        voice.failPcmStream(streamId, payload.error || 'JoyAI 语音流失败');
      });
      try {
        reportRealtimeEvent('realtime_tts_synthesis_started', {
          text_chars: spokenText.length,
          stream_id: streamId,
        });
        await webRequest(
          'tts.stream.start',
          { text: spokenText, stream_id: streamId },
          { timeoutMs: 10_000 },
        );
        streamStarted = true;
        await playback;
        if (
          !canPlayJoyAIResponse(
            generation,
            joyaiTtsGenerationRef.current,
            joyaiUserSpeechActiveRef.current,
          )
          || !joyaiActiveRef.current
        ) return;
        reportRealtimeEvent('realtime_tts_playback_completed', {
          text_chars: spokenText.length,
          stream_id: streamId,
          streamed: true,
        });
      } catch (ttsError) {
        if (
          !canPlayJoyAIResponse(
            generation,
            joyaiTtsGenerationRef.current,
            joyaiUserSpeechActiveRef.current,
          )
          || !joyaiActiveRef.current
        ) return;
        voice.interruptPlayback();
        if (!streamStarted && !receivedStreamAudio) {
          try {
            const result = await webRequest<{
              audio_base64?: string;
              audio_mime?: string;
            }>('tts.synthesize', { text: spokenText }, { timeoutMs: 60_000 });
            if (
              !canPlayJoyAIResponse(
                generation,
                joyaiTtsGenerationRef.current,
                joyaiUserSpeechActiveRef.current,
              )
              || !joyaiActiveRef.current
              || joyaiVoiceRef.current !== voice
              || !result.audio_base64
            ) return;
            await voice.speak(
              `data:${result.audio_mime || 'audio/wav'};base64,${result.audio_base64}`,
            );
            reportRealtimeEvent('realtime_tts_playback_completed', {
              text_chars: spokenText.length,
              stream_id: streamId,
              streamed: false,
            });
            return;
          } catch (fallbackError) {
            ttsError = fallbackError;
          }
        }
        const message = ttsError instanceof Error ? ttsError.message : 'JoyAI 语音合成失败';
        reportRealtimeEvent('realtime_tts_failed', {
          text_chars: spokenText.length,
          stream_id: streamId,
          message,
        });
        setError(message);
      } finally {
        unsubscribeChunk();
        unsubscribeDone();
        unsubscribeCancelled();
        unsubscribeError();
        if (joyaiActiveTtsStreamRef.current === streamId) {
          joyaiActiveTtsStreamRef.current = '';
        }
      }
    };
    const queued = joyaiTtsQueueRef.current.then(play, play);
    joyaiTtsQueueRef.current = queued.then(() => undefined, () => undefined);
  };

  const commitAssistantAnswer = (
    text: string,
    toolJobId?: string,
    turnId?: string,
    ttsGeneration = joyaiTtsGenerationRef.current,
  ) => {
    const normalized = cleanAssistantText(text);
    if (!normalized) return;
    if (turnId) {
      assistantAnswersByTurnRef.current.set(turnId, normalized);
      while (assistantAnswersByTurnRef.current.size > 16) {
        const oldest = assistantAnswersByTurnRef.current.keys().next().value;
        if (!oldest) break;
        assistantAnswersByTurnRef.current.delete(oldest);
      }
    }
    answerRef.current = normalized;
    streamingAnswerRef.current = '';
    streamingToolJobIdRef.current = undefined;
    streamingAnswerTurnIdRef.current = undefined;
    setStreamingAnswer('');
    setIsAwaitingVoiceTranscript(false);
    setAnswer(normalized);
    if (pendingTranscriptionsRef.current > 0) {
      deferredAssistantAnswersRef.current.push({ text: normalized, toolJobId });
    } else {
      appendChat('assistant', normalized);
    }
    if (toolJobId) {
      searchJobsRef.current.delete(toolJobId);
      if (![...searchJobsRef.current.values()].some((job) => job.status !== 'failed')) {
        setToolStatus('');
      }
    }
    if (joyaiActiveRef.current) {
      if (!canPlayJoyAIResponse(
        ttsGeneration,
        joyaiTtsGenerationRef.current,
        joyaiUserSpeechActiveRef.current,
      )) {
        reportRealtimeEvent('joyai_tts_suppressed_for_barge_in', {
          user_speech_active: joyaiUserSpeechActiveRef.current,
          response_generation: ttsGeneration,
          current_generation: joyaiTtsGenerationRef.current,
          text_chars: normalized.length,
        });
      } else {
        void speakJoyAIText(normalized, ttsGeneration);
      }
    }
  };

  const flushDeferredAssistantAnswers = () => {
    const deferred = deferredAssistantAnswersRef.current;
    deferredAssistantAnswersRef.current = [];
    deferred.forEach(({ text }) => appendChat('assistant', text));
  };

  const applyCurrentTask = (task: string) => {
    const normalized = task.trim();
    if (normalized === currentTaskRef.current) return;
    const previousTask = currentTaskRef.current;
    currentTaskRef.current = normalized;
    setCurrentTask(normalized);
    duplexRef.current?.updateContext(normalized, recentChatRef.current);
    void webRequest('video.realtime.telemetry', {
      event: 'current_task_applied',
      previous_task: previousTask,
      current_task: normalized,
    }, { timeoutMs: 5_000 }).catch(() => undefined);
  };

  const rememberSearchJob = (job: AgentAction['search_job']) => {
    const id = job?.id?.trim();
    if (!id) return;
    const existing = searchJobsRef.current.get(id);
    if (existing?.status === 'queued' || existing?.status === 'failed') return;
    searchJobsRef.current.set(id, {
      id,
      searchSessionId: job?.search_session_id?.trim() || searchSessionRef.current,
      question: job?.question?.trim() || '',
      query: job?.query?.trim() || '',
      status: 'running',
      frameDataUrl: existing?.frameDataUrl || framesRef.current.at(-1)?.data_url,
    });
    setToolStatus('正在后台搜索，可继续提问…');
  };

  const requestJoyAIFrame = async (
    instruction: string,
    originalQuestion = '',
    options: {
      skipIfBusy?: boolean;
      toolJobId?: string;
      frameDataUrl?: string;
      required?: boolean;
      joyaiSessionId?: string;
      commitResponse?: boolean;
      requestKind?: 'user' | 'monitor' | 'tool';
      frameOnly?: boolean;
    } = {},
  ): Promise<JoyAIFrameResult | null> => {
    if (!joyaiActiveRef.current) {
      if (options.required) throw new Error('JoyAI 会话已停止，无法回填搜索结果');
      return null;
    }
    const activeSessionId = joyaiSessionIdRef.current;
    const sessionId = options.joyaiSessionId || activeSessionId;
    const frameDataUrl = options.frameDataUrl || framesRef.current.at(-1)?.data_url || '';
    if (!sessionId || !frameDataUrl) {
      if (options.required) throw new Error('没有可用于搜索结果回填的 JoyAI 会话画面');
      return null;
    }
    if (options.skipIfBusy && joyaiQueuedRequestCountRef.current > 0) return null;

    const ttsGenerationAtRequest = joyaiTtsGenerationRef.current;
    joyaiQueuedRequestCountRef.current += 1;
    const execute = async (): Promise<JoyAIFrameResult | null> => {
      if (!joyaiActiveRef.current || joyaiSessionIdRef.current !== activeSessionId) {
        if (options.required) throw new Error('JoyAI 会话在搜索期间已结束或被替换');
        return null;
      }
      joyaiRequestInFlightRef.current = true;
      try {
        const requestKind = options.requestKind || (originalQuestion ? 'user' : 'monitor');
        const prompt = options.frameOnly ? '' : instruction.trim();
        const sessionElapsedSeconds = joyaiSessionStartedAtRef.current > 0
          ? Math.max(0, (performance.now() - joyaiSessionStartedAtRef.current) / 1_000)
          : 0;
        const frameRangeStart = Math.min(joyaiLastFrameTimeRef.current, sessionElapsedSeconds);
        joyaiLastFrameTimeRef.current = sessionElapsedSeconds;
        const frameTimeRange = `${frameRangeStart.toFixed(1)} seconds ~ ${sessionElapsedSeconds.toFixed(1)} seconds`;
        const result = await webRequest<JoyAIFrameResult>('video.joyai.frame', {
          frame_data_url: frameDataUrl,
          instruction: prompt.slice(0, 2_000),
          question: originalQuestion.slice(0, 500),
          request_kind: requestKind,
          joyai_session_id: sessionId,
          search_session_id: searchSessionRef.current,
          frame_time_range: frameTimeRange,
        }, { timeoutMs: 60_000 });
        if (!joyaiActiveRef.current || joyaiSessionIdRef.current !== activeSessionId) {
          if (options.required) throw new Error('JoyAI 会话在搜索结果返回前已结束或被替换');
          return null;
        }
        const response = result.response?.trim() || '';
        if (response && options.commitResponse !== false) {
          commitAssistantAnswer(response, options.toolJobId, undefined, ttsGenerationAtRequest);
        }
        rememberSearchJob(result.search_job);
        return result;
      } finally {
        joyaiRequestInFlightRef.current = false;
      }
    };
    const queuedRequest = joyaiRequestQueueRef.current.then(execute, execute);
    joyaiRequestQueueRef.current = queuedRequest.then(() => undefined, () => undefined);
    try {
      return await queuedRequest;
    } finally {
      joyaiQueuedRequestCountRef.current = Math.max(
        0,
        joyaiQueuedRequestCountRef.current - 1,
      );
    }
  };

  const startJoyAIMonitor = () => {
    if (joyaiMonitorTimerRef.current !== null) return;
    reportRealtimeEvent('joyai_monitor_started', {
      client_build: JOYAI_MONITOR_CLIENT_BUILD,
      frame_count: framesRef.current.length,
    });
    const sendLatestFrame = () => {
      if (!joyaiActiveRef.current) return;
      void requestJoyAIFrame('', '', {
        skipIfBusy: true,
        requestKind: 'monitor',
        frameOnly: true,
      }).catch((monitorError) => {
        setError(monitorError instanceof Error ? monitorError.message : 'JoyAI 监控请求失败');
      });
    };
    sendLatestFrame();
    joyaiMonitorTimerRef.current = window.setInterval(
      sendLatestFrame,
      JOYAI_MONITOR_INTERVAL_MS,
    );
  };

  const submitJoyAIUserInstruction = async (text: string): Promise<JoyAIFrameResult | null> => {
    applyCurrentTask(text);
    return requestJoyAIFrame(text, text, {
      requestKind: 'user',
    });
  };

  const recentChatForRouter = () => recentChatRef.current
    .slice(-8)
    .map((item) => `${item.role === 'user' ? '用户' : item.role === 'assistant' ? 'Realtime助手' : '工具'}：${item.text}`)
    .join('\n');

  const waitForJoyAIAnswerSlot = async (sessionId: string): Promise<boolean> => {
    while (joyaiActiveRef.current && joyaiSessionIdRef.current === sessionId) {
      const requestBarrier = joyaiRequestQueueRef.current;
      await requestBarrier;
      const ttsBarrier = joyaiTtsQueueRef.current;
      await ttsBarrier;
      if (
        pendingTranscriptionsRef.current === 0
        && !joyaiUserSpeechActiveRef.current
        && joyaiQueuedRequestCountRef.current === 0
        && requestBarrier === joyaiRequestQueueRef.current
        && ttsBarrier === joyaiTtsQueueRef.current
      ) return true;
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
    return false;
  };

  const acceptCompletedSearch = (payload: SearchJobPayload) => {
    if (!payload.job_id || payload.search_session_id !== searchSessionRef.current) return;
    if (acceptedSearchJobIdsRef.current.has(payload.job_id)) {
      reportRealtimeEvent('search_result_duplicate_ignored', {
        job_id: payload.job_id,
        search_session_id: payload.search_session_id || '',
      });
      return;
    }
    const existing = searchJobsRef.current.get(payload.job_id);
    if (existing?.status === 'queued') return;
    const result = payload.result?.trim() || '';
    const question = payload.question?.trim() || existing?.question || '';
    reportRealtimeEvent('search_result_received', {
      job_id: payload.job_id,
      search_session_id: payload.search_session_id || '',
      question,
      query: payload.query?.trim() || existing?.query || '',
      result,
    });
    if (!result || !question) {
      reportRealtimeEvent('search_result_queue_failed', {
        job_id: payload.job_id,
        message: !result ? 'empty search result' : 'missing original question',
      });
      return;
    }
    // Claim the job before asynchronous delivery starts: WebSocket completion and
    // status polling may report the same completed job in the same event loop turn.
    acceptedSearchJobIdsRef.current.add(payload.job_id);
    if (joyaiActiveRef.current) {
      const sessionId = joyaiSessionIdRef.current;
      searchJobsRef.current.set(payload.job_id, {
        id: payload.job_id,
        searchSessionId: payload.search_session_id || existing?.searchSessionId || '',
        question,
        query: payload.query?.trim() || existing?.query || '',
        status: 'queued',
      });
      const groundedAnswer = groundedSearchAnswer(result);
      appendChat('tool', `${payload.engine || '免费搜索'}搜索完成`);
      setToolStatus('搜索完成，等待当前回答结束后展示…');
      reportRealtimeEvent('search_result_waiting_for_output_slot', {
        job_id: payload.job_id,
        message: 'Grounded search answer queued behind active user requests and speech',
      });
      const deliver = async () => {
        if (!await waitForJoyAIAnswerSlot(sessionId)) return;
        commitAssistantAnswer(groundedAnswer || result, payload.job_id);
        setToolStatus('');
        reportRealtimeEvent('search_result_answered', {
          job_id: payload.job_id,
          realtime_answer: groundedAnswer || result,
          message: 'Displayed after earlier JoyAI requests and speech completed',
        });
        const groundedInstruction = [
          '以下是搜索工具和主对话模型已经交付给用户的可靠资料。请记住这些资料供后续追问使用，不要再次回答。',
          `原问题：${question}`,
          `搜索结果：${result}`,
        ].join('\n').slice(0, 2_000);
        try {
          await requestJoyAIFrame(groundedInstruction, question, {
            frameDataUrl: existing?.frameDataUrl,
            commitResponse: false,
            requestKind: 'tool',
          });
          reportRealtimeEvent('search_result_dispatched', {
            job_id: payload.job_id,
            message: 'Grounded search context synchronized to JoyAI',
          });
        } catch (searchAnswerError) {
          const message = searchAnswerError instanceof Error ? searchAnswerError.message : '请重试';
          reportRealtimeEvent('search_result_response_empty', {
            job_id: payload.job_id,
            message: `Grounded answer displayed, but JoyAI context synchronization failed: ${message}`,
          });
        }
        await joyaiTtsQueueRef.current;
      };
      const queuedDelivery = joyaiSearchDeliveryQueueRef.current.then(deliver, deliver);
      joyaiSearchDeliveryQueueRef.current = queuedDelivery.then(
        () => undefined,
        () => undefined,
      );
      return;
    }
    const queued = duplexRef.current?.enqueueToolResult({
      jobId: payload.job_id,
      question,
      result,
    }) || false;
    searchJobsRef.current.set(payload.job_id, {
      id: payload.job_id,
      searchSessionId: payload.search_session_id || existing?.searchSessionId || '',
      question,
      query: payload.query?.trim() || existing?.query || '',
      // A failed delivery stays recoverable and will be retried by status polling.
      status: queued ? 'queued' : 'running',
    });
    if (queued) {
      appendChat('tool', result);
      setToolStatus(`${payload.engine || '免费搜索'}完成，等待模型空闲后回答…`);
    } else {
      reportRealtimeEvent('search_result_queue_failed', {
        job_id: payload.job_id,
        message: duplexRef.current ? 'tool result rejected' : 'realtime session unavailable',
      });
      setToolStatus('搜索结果暂未回填，正在重试…');
    }
  };

  const acceptFailedSearch = (payload: SearchJobPayload) => {
    if (!payload.job_id || payload.search_session_id !== searchSessionRef.current) return;
    const existing = searchJobsRef.current.get(payload.job_id);
    searchJobsRef.current.set(payload.job_id, {
      id: payload.job_id,
      searchSessionId: payload.search_session_id || existing?.searchSessionId || '',
      question: payload.question?.trim() || existing?.question || '',
      query: payload.query?.trim() || existing?.query || '',
      status: 'failed',
    });
    setToolStatus(`${payload.engine || '九问免费搜索'}失败：${payload.error || '请重试'}`);
  };

  const waitForRealtimeTurnAnswer = async (turnId: string, timeoutMs = 20_000): Promise<string> => {
    const deadline = Date.now() + timeoutMs;
    while (!assistantAnswersByTurnRef.current.has(turnId) && Date.now() < deadline) {
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
    const answerForTurn = assistantAnswersByTurnRef.current.get(turnId) || '';
    assistantAnswersByTurnRef.current.delete(turnId);
    return answerForTurn;
  };

  useEffect(() => {
    const belongsToCurrentSession = (payload: SearchJobPayload) => (
      Boolean(searchSessionRef.current)
      && payload.search_session_id === searchSessionRef.current
    );
    const unsubscribeStarted = webClient.on<SearchJobPayload>('video.search.started', ({ payload }) => {
      if (!belongsToCurrentSession(payload) || !payload.job_id) return;
      searchJobsRef.current.set(payload.job_id, {
        id: payload.job_id,
        searchSessionId: payload.search_session_id || searchSessionRef.current,
        question: payload.question?.trim() || '',
        query: payload.query?.trim() || '',
        status: 'running',
        frameDataUrl: framesRef.current.at(-1)?.data_url,
      });
      setToolStatus(`正在使用${payload.engine || '九问免费搜索'}，可继续提问…`);
    });
    const unsubscribeCompleted = webClient.on<SearchJobPayload>('video.search.completed', ({ payload }) => {
      if (belongsToCurrentSession(payload)) acceptCompletedSearch(payload);
    });
    const unsubscribeFailed = webClient.on<SearchJobPayload>('video.search.failed', ({ payload }) => {
      if (belongsToCurrentSession(payload)) acceptFailedSearch(payload);
    });
    const pollTimer = window.setInterval(() => {
      searchJobsRef.current.forEach((job) => {
        if (job.status !== 'running' || pollingSearchJobsRef.current.has(job.id)) return;
        pollingSearchJobsRef.current.add(job.id);
        void webRequest<SearchJobPayload>('video.search.status', {
          job_id: job.id,
          search_session_id: job.searchSessionId,
        }, { timeoutMs: 5_000 })
          .then((payload) => {
            if (payload.status === 'completed') acceptCompletedSearch(payload);
            if (payload.status === 'failed') acceptFailedSearch(payload);
          })
          .catch(() => undefined)
          .finally(() => pollingSearchJobsRef.current.delete(job.id));
      });
    }, 1_000);
    return () => {
      unsubscribeStarted();
      unsubscribeCompleted();
      unsubscribeFailed();
      window.clearInterval(pollTimer);
    };
  }, []);

  useEffect(() => {
    const history = chatHistoryRef.current;
    if (history) history.scrollTop = history.scrollHeight;
  }, [chatHistory, streamingAnswer]);

  const releaseSource = useCallback(() => {
    cameraStreamRef.current?.getTracks().forEach((track) => track.stop());
    cameraStreamRef.current = null;
    if (fileUrlRef.current) {
      URL.revokeObjectURL(fileUrlRef.current);
      fileUrlRef.current = null;
    }
    const video = videoRef.current;
    if (video) {
      video.pause();
      video.srcObject = null;
      video.removeAttribute('src');
      video.load();
    }
    framesRef.current = [];
    setFrameCount(0);
    setIsPlaying(false);
  }, []);

  const releaseScreens = useCallback((updateState = true) => {
    screenStreamsRef.current.forEach((stream) => {
      stream.getTracks().forEach((track) => track.stop());
    });
    screenStreamsRef.current.clear();
    screenVideoRefs.current.clear();
    if (updateState) setScreens([]);
  }, []);

  const closeSource = () => {
    stopModelTransport();
    setIsRecording(false);
    releaseScreens();
    releaseSource();
    setSource(null);
    setSourceName('');
    resetVisualContext();
    setError('');
  };

  useEffect(() => () => {
    stopModelTransport();
    releaseScreens(false);
    releaseSource();
  }, [releaseScreens, releaseSource]);

  useEffect(() => {
    if (!isPlaying) return;

    let cancelled = false;
    let captureInFlight = false;

    const canvasToDataUrl = (canvas: HTMLCanvasElement): Promise<string | null> => new Promise((resolve) => {
      canvas.toBlob((blob) => {
        if (!blob || cancelled) {
          resolve(null);
          return;
        }
        const reader = new FileReader();
        reader.onload = () => resolve(typeof reader.result === 'string' ? reader.result : null);
        reader.onerror = () => resolve(null);
        reader.readAsDataURL(blob);
      }, 'image/jpeg', FRAME_JPEG_QUALITY);
    });

    const captureVideo = async (
      video: HTMLVideoElement,
      sourceId: string,
      sourceLabel: string,
    ): Promise<CapturedFrame | null> => {
      const canvas = canvasRef.current;
      if (!canvas || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return null;
      if (!video.videoWidth || !video.videoHeight) return null;

      const scale = Math.min(1, MAX_FRAME_WIDTH / video.videoWidth);
      const width = Math.round(video.videoWidth * scale);
      const height = Math.round(video.videoHeight * scale);
      if (canvas.width !== width) canvas.width = width;
      if (canvas.height !== height) canvas.height = height;
      const context = canvas.getContext('2d');
      if (!context) return null;

      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const dataUrl = await canvasToDataUrl(canvas);
      if (!dataUrl || cancelled) return null;
      return {
        data_url: dataUrl,
        source_id: sourceId,
        source_label: sourceLabel,
      };
    };

    const capture = async () => {
      if (captureInFlight || cancelled) return;
      captureInFlight = true;
      const captured: CapturedFrame[] = [];
      try {
        if (source === 'screen') {
          for (const screen of screens) {
            if (cancelled) break;
            const video = screenVideoRefs.current.get(screen.id);
            if (!video) continue;
            const frame = await captureVideo(video, screen.id, screen.name);
            if (frame) captured.push(frame);
          }
        } else {
          const video = videoRef.current;
          if (video) {
            const frame = await captureVideo(
              video,
              source || 'video',
              source === 'camera' ? '摄像头' : sourceName || '本地视频',
            );
            if (frame) captured.push(frame);
          }
        }
        if (cancelled || captured.length === 0) return;
        framesRef.current.push(...captured);
        if (framesRef.current.length > MAX_FRAMES) {
          framesRef.current.splice(0, framesRef.current.length - MAX_FRAMES);
        }
        setFrameCount(framesRef.current.length);
      } finally {
        captureInFlight = false;
      }
    };

    void capture();
    const timer = window.setInterval(() => void capture(), FRAME_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [isPlaying, screens, source, sourceName]);

  const startCamera = async () => {
    stopModelTransport();
    setIsRecording(false);
    releaseScreens();
    releaseSource();
    setSource(null);
    setSourceName('');
    resetVisualContext();
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: { width: { ideal: 1280 }, height: { ideal: 720 } },
        audio: false,
      });
      cameraStreamRef.current = stream;
      setSource('camera');
      setSourceName('Camera');
      const video = videoRef.current;
      if (!video) {
        releaseSource();
        setSource(null);
        setSourceName('');
        return;
      }
      video.srcObject = stream;
      video.muted = true;
      try {
        await video.play();
        setIsPlaying(true);
      } catch (playError) {
        setError(
          playError instanceof Error
            ? `摄像头已连接，但画面播放失败：${playError.message}`
            : '摄像头已连接，但画面播放失败。',
        );
      }
    } catch (cameraError) {
      releaseSource();
      setSource(null);
      setSourceName('');
      setError(cameraError instanceof Error ? cameraError.message : '无法打开摄像头');
    }
  };

  const openFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;

    stopModelTransport();
    setIsRecording(false);
    releaseScreens();
    releaseSource();
    resetVisualContext();
    setError('');
    const url = URL.createObjectURL(file);
    fileUrlRef.current = url;
    const video = videoRef.current;
    if (!video) return;
    video.srcObject = null;
    video.src = url;
    // Local video is visual context, not microphone input. Keeping it muted
    // also allows reliable autoplay and prevents speaker audio feeding ASR.
    video.muted = true;
    await video.play().catch(() => undefined);
    setSource('file');
    setSourceName(file.name);
    setIsPlaying(!video.paused);
  };

  const removeScreen = useCallback((screenId: string) => {
    const stream = screenStreamsRef.current.get(screenId);
    stream?.getTracks().forEach((track) => track.stop());
    screenStreamsRef.current.delete(screenId);
    screenVideoRefs.current.delete(screenId);
    framesRef.current = framesRef.current.filter((frame) => frame.source_id !== screenId);
    setFrameCount(framesRef.current.length);
    setScreens((current) => {
      const next = current.filter((screen) => screen.id !== screenId);
      if (next.length === 0) {
        setSource(null);
        setSourceName('');
        setIsPlaying(false);
      } else {
        setSourceName(`${next.length} 个屏幕`);
      }
      return next;
    });
  }, []);

  const startScreen = async () => {
    if (screens.length >= MAX_SCREENS) {
      setError(`最多同时读取 ${MAX_SCREENS} 个屏幕。`);
      return;
    }
    if (!navigator.mediaDevices?.getDisplayMedia) {
      setError('当前运行环境不支持屏幕共享。');
      return;
    }

    setError('');
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: { frameRate: { ideal: SCREEN_PREVIEW_FRAME_RATE, max: SCREEN_PREVIEW_FRAME_RATE } },
        audio: false,
      });
      const track = stream.getVideoTracks()[0];
      if (!track) {
        stream.getTracks().forEach((item) => item.stop());
        throw new Error('没有获得屏幕画面。');
      }

      if (source !== 'screen') {
        stopModelTransport();
        setIsRecording(false);
        releaseSource();
        releaseScreens();
        resetVisualContext();
      }

      const screenId = typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `screen-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const name = track.label.trim() || `屏幕 ${screens.length + 1}`;
      screenStreamsRef.current.set(screenId, stream);
      setScreens((current) => {
        setSourceName(`${current.length + 1} 个屏幕`);
        return [...current, { id: screenId, name, stream }];
      });
      setSource('screen');
      setIsPlaying(true);
      track.addEventListener('ended', () => removeScreen(screenId), { once: true });
    } catch (screenError) {
      if (screenError instanceof DOMException && screenError.name === 'NotAllowedError') {
        setError('已取消选择屏幕。');
      } else {
        setError(screenError instanceof Error ? screenError.message : '无法读取屏幕。');
      }
    }
  };

  const startRealtime = async () => {
    if (duplexRef.current || joyaiActiveRef.current) return;
    if (startingRealtimeRef.current) return startingRealtimeRef.current;
    const start = (async () => {
    setIsRealtimeStarting(true);
    reportRealtimeEvent('realtime_start_clicked', {
      source: source || 'none',
      frame_count: framesRef.current.length,
    });
    if (framesRef.current.length === 0) {
      if (!source) {
        const message = '请先打开摄像头、视频或共享屏幕。';
        setError(message);
        setRealtimeStatus('');
        setIsRealtimeStarting(false);
        reportRealtimeEvent('realtime_start_blocked_no_source');
        return;
      }
      setError('');
      setRealtimeStatus('正在等待视频首帧…');
      reportRealtimeEvent('realtime_first_frame_waiting', { source });
      if (!await waitForFirstFrame()) {
        const message = '尚未读取到视频画面，请确认画面正在播放后重试。';
        setError(message);
        setRealtimeStatus('');
        setIsRealtimeStarting(false);
        reportRealtimeEvent('realtime_start_blocked_no_frames', { source });
        return;
      }
      reportRealtimeEvent('realtime_first_frame_ready', {
        source,
        frame_count: framesRef.current.length,
      });
    }
    setError('');
    setRealtimeStatus('正在读取视频模型配置…');
    reportRealtimeEvent('realtime_config_requested');
    try {
      searchSessionRef.current = crypto.randomUUID();
      acceptedSearchJobIdsRef.current.clear();
      const config = await webRequest<VideoSessionConfig>('video.realtime.config', {});
      reportRealtimeEvent('realtime_config_received', {
        model: config.model,
        provider: config.provider || 'realtime',
      });
      setModel(config.model);
      if (config.provider === 'joyai') {
        applyCurrentTask('');
        const sessionId = crypto.randomUUID();
        joyaiSessionIdRef.current = sessionId;
        joyaiActiveRef.current = true;
        joyaiUserSpeechActiveRef.current = false;
        joyaiUserSpeechEpochRef.current += 1;
        joyaiSessionStartedAtRef.current = performance.now();
        joyaiLastFrameTimeRef.current = 0;
        // Visual monitoring is independent of microphone initialization. Start it
        // immediately so a slow or unavailable microphone cannot stop frame flow.
        startJoyAIMonitor();
        const voice = new JoyAIVoiceSession({
          onSpeechStart: () => {
            joyaiUserSpeechActiveRef.current = true;
            joyaiUserSpeechEpochRef.current += 1;
            interruptJoyAITts();
            agentRequestVersionRef.current += 1;
            reportRealtimeEvent('joyai_barge_in_started', {
              speech_epoch: joyaiUserSpeechEpochRef.current,
              tts_generation: joyaiTtsGenerationRef.current,
            });
          },
          onTurnAudio: (audioDataUrl, turnId) => {
            if (handledAgentTurnsRef.current.has(turnId)) return;
            handledAgentTurnsRef.current.add(turnId);
            const speechEpochAtTurn = joyaiUserSpeechEpochRef.current;
            let releasedForInstruction = false;
            pendingTranscriptionsRef.current += 1;
            setIsAwaitingVoiceTranscript(true);
            void (async () => {
              try {
                const asr = await webRequest<{ transcript?: string }>('video.transcribe', {
                  audio_data_url: audioDataUrl,
                }, { timeoutMs: 45_000 });
                if (!joyaiActiveRef.current || joyaiSessionIdRef.current !== sessionId) return;
                const transcript = asr.transcript?.trim();
                if (!transcript) return;
                appendChat('user', transcript);
                setIsAwaitingVoiceTranscript(false);
                if (speechEpochAtTurn === joyaiUserSpeechEpochRef.current) {
                  // Invalidate monitor/search responses created while the user was
                  // speaking before allowing the new user request to produce audio.
                  interruptJoyAITts();
                  joyaiUserSpeechActiveRef.current = false;
                  releasedForInstruction = true;
                  reportRealtimeEvent('joyai_barge_in_released_for_instruction', {
                    speech_epoch: speechEpochAtTurn,
                    tts_generation: joyaiTtsGenerationRef.current,
                    transcript_chars: transcript.length,
                  });
                }
                await submitJoyAIUserInstruction(transcript);
              } catch (voiceError) {
                if (!joyaiActiveRef.current || joyaiSessionIdRef.current !== sessionId) return;
                setError(voiceError instanceof Error ? voiceError.message : 'JoyAI 语音处理失败');
              } finally {
                if (
                  !releasedForInstruction
                  && speechEpochAtTurn === joyaiUserSpeechEpochRef.current
                ) {
                  interruptJoyAITts();
                  joyaiUserSpeechActiveRef.current = false;
                  reportRealtimeEvent('joyai_barge_in_released_without_instruction', {
                    speech_epoch: speechEpochAtTurn,
                    tts_generation: joyaiTtsGenerationRef.current,
                  });
                }
                pendingTranscriptionsRef.current = Math.max(
                  0,
                  pendingTranscriptionsRef.current - 1,
                );
                if (pendingTranscriptionsRef.current === 0) {
                  setIsAwaitingVoiceTranscript(false);
                  flushDeferredAssistantAnswers();
                }
              }
            })();
          },
          onState: (state) => {
            if (state === 'closed') {
              setIsRecording(false);
              setRealtimeStatus('');
              return;
            }
            setIsRecording(true);
            setRealtimeStatus(state === 'connecting'
              ? '正在申请麦克风权限…'
              : state === 'speaking'
                ? '模型正在回答…'
                : '');
            if (state === 'listening') setIsRealtimeStarting(false);
          },
          onError: setError,
        });
        joyaiVoiceRef.current = voice;
        setRealtimeStatus('正在申请麦克风权限…');
        await voice.start();
        return;
      }
      if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
        reportRealtimeEvent('realtime_start_unsupported_browser');
        throw new Error('当前浏览器不支持 Full-duplex 音频。');
      }
      if (!config.url) throw new Error('请配置 Full-duplex WebSocket 地址');
      if (!config.ref_audio_base64) throw new Error('请配置 Full-duplex 参考音频');
      const session = new RealtimeDuplexSession({
        url: config.url,
        model: config.model,
        refAudio: `data:audio/wav;base64,${config.ref_audio_base64}`,
      }, {
        getVideoFrame: () => framesRef.current.at(-1)?.data_url.split(',', 2)[1] || null,
        onAssistantText: (text, final, toolJobId, turnId) => {
          const visibleText = cleanAssistantText(text);
          if (!visibleText) return;
          answerRef.current = visibleText;
          if (!final) {
            streamingAnswerRef.current = visibleText;
            streamingToolJobIdRef.current = toolJobId;
            streamingAnswerTurnIdRef.current = turnId;
            setStreamingAnswer(visibleText);
          } else {
            commitAssistantAnswer(visibleText, toolJobId, turnId);
          }
        },
        onUserText: () => undefined,
        onUserActivity: () => {
          agentRequestVersionRef.current += 1;
          const interruptedAnswer = streamingAnswerRef.current;
          if (interruptedAnswer) {
            commitAssistantAnswer(
              interruptedAnswer,
              streamingToolJobIdRef.current,
              streamingAnswerTurnIdRef.current,
            );
          } else {
            streamingToolJobIdRef.current = undefined;
            streamingAnswerTurnIdRef.current = undefined;
            setStreamingAnswer('');
          }
        },
        onTurnAudio: (audioDataUrl, turnId) => {
          if (handledAgentTurnsRef.current.has(turnId)) return;
          handledAgentTurnsRef.current.add(turnId);
          if (agentDebounceTimerRef.current !== null) {
            window.clearTimeout(agentDebounceTimerRef.current);
            agentDebounceTimerRef.current = null;
          }
          if (handledAgentTurnsRef.current.size > 8) {
            const oldest = handledAgentTurnsRef.current.values().next().value;
            if (oldest) handledAgentTurnsRef.current.delete(oldest);
          }
          const segmentOrder = ++agentSegmentOrderRef.current;
          const requestVersionAtTurn = agentRequestVersionRef.current;
          pendingTranscriptionsRef.current += 1;
          setIsAwaitingVoiceTranscript(true);
          void (async () => {
            let transcriptDisplayed = false;
            try {
              const asr = await webRequest<{ transcript?: string }>('video.transcribe', {
                audio_data_url: audioDataUrl,
              }, { timeoutMs: 45_000 });
              const transcript = asr.transcript?.trim();
              if (!transcript) return;
              latestMeaningfulAgentVersionRef.current = advanceMeaningfulVideoAgentVersion(
                latestMeaningfulAgentVersionRef.current,
                requestVersionAtTurn,
                transcript,
              );
              appendChat('user', transcript);
              transcriptDisplayed = true;
              setIsAwaitingVoiceTranscript(false);
              const realtimeAnswer = await waitForRealtimeTurnAnswer(turnId);
              agentSegmentsRef.current.push({
                order: segmentOrder,
                text: transcript,
                realtimeAnswer,
                requestVersion: requestVersionAtTurn,
              });
            } catch {
              // Realtime 主链不因辅助工具失败而中断。
            } finally {
              if (!transcriptDisplayed) setIsAwaitingVoiceTranscript(false);
              pendingTranscriptionsRef.current = Math.max(0, pendingTranscriptionsRef.current - 1);
              if (pendingTranscriptionsRef.current === 0) {
                if (agentDebounceTimerRef.current !== null) window.clearTimeout(agentDebounceTimerRef.current);
                agentDebounceTimerRef.current = window.setTimeout(() => {
                  agentDebounceTimerRef.current = null;
                  const turns = collectVideoAgentTurns(agentSegmentsRef.current);
                  agentSegmentsRef.current = [];
                  if (turns.length === 0) {
                    flushDeferredAssistantAnswers();
                    return;
                  }
                  flushDeferredAssistantAnswers();
                  turns.forEach(({ version, question: turnQuestion, realtimeAnswer }) => {
                    void (async () => {
                      try {
                        const action = await webRequest<AgentAction>('video.agent', {
                          question: turnQuestion,
                          realtime_answer: realtimeAnswer,
                          current_task: currentTaskRef.current,
                          recent_chat: recentChatForRouter(),
                          search_session_id: searchSessionRef.current,
                        }, { timeoutMs: 45_000 });
                        const isLatestTurn = version === latestMeaningfulAgentVersionRef.current;
                        if (isLatestTurn && (action.current_task || action.tools_used?.includes('stop_current_task'))) {
                          applyCurrentTask(action.current_task || '');
                        }
                        rememberSearchJob(action.search_job);
                      } catch {
                        setToolStatus('意图识别失败，Full-duplex 对话仍可继续');
                      }
                    })();
                  });
                }, AGENT_DEBOUNCE_MS);
              }
            }
          })();
        },
        onState: (state) => {
          setIsRecording(state !== 'closed');
          setRealtimeStatus(state === 'connecting'
            ? '正在连接 Full-duplex 模型并申请麦克风权限…'
            : state === 'listening'
              ? ''
              : state === 'speaking'
                ? '模型正在回答…'
                : '');
          if (state === 'listening') setIsRealtimeStarting(false);
          if (state === 'speaking') setAnswer((current) => current || '正在回答…');
        },
        onError: setError,
        onToolResultDispatched: () => {
          setToolStatus('搜索结果已交给模型，正在组织回答…');
        },
        onDiagnostic: (event) => {
          void webRequest('video.realtime.telemetry', event, { timeoutMs: 5_000 }).catch(() => undefined);
        },
      });
      duplexRef.current = session;
      session.updateContext(currentTaskRef.current, recentChatRef.current);
      await session.start();
    } catch (realtimeError) {
      const wasJoyAI = joyaiActiveRef.current;
      stopModelTransport();
      if (wasJoyAI) applyCurrentTask('');
      setIsRecording(false);
      setRealtimeStatus('');
      setIsRealtimeStarting(false);
      searchSessionRef.current = '';
      searchJobsRef.current.clear();
      acceptedSearchJobIdsRef.current.clear();
      setError(realtimeError instanceof Error ? realtimeError.message : 'Full-duplex 会话启动失败。');
    }
    })();
    startingRealtimeRef.current = start;
    try {
      await start;
    } finally {
      startingRealtimeRef.current = null;
    }
  };

  const stopRealtime = () => {
    const wasJoyAI = joyaiActiveRef.current;
    stopModelTransport();
    if (wasJoyAI) applyCurrentTask('');
    searchSessionRef.current = '';
    searchJobsRef.current.clear();
    acceptedSearchJobIdsRef.current.clear();
    setIsRecording(false);
    setIsRealtimeStarting(false);
    setRealtimeStatus('');
  };

  const sendText = async (event: FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text) return;
    if (!duplexRef.current && !joyaiActiveRef.current) await startRealtime();
    if (!duplexRef.current && !joyaiActiveRef.current) return;
    const version = ++agentRequestVersionRef.current;
    latestMeaningfulAgentVersionRef.current = version;
    try {
      appendChat('user', text);
      setQuestion('');
      if (joyaiActiveRef.current) {
        const result = await submitJoyAIUserInstruction(text);
        if (!result) throw new Error('文字输入未进入 JoyAI 会话');
        return;
      }
      const turnId = await duplexRef.current?.sendTextTurn(text);
      if (!turnId) throw new Error('文字输入未进入 Full-duplex 会话');
      const realtimeAnswer = await waitForRealtimeTurnAnswer(turnId);
      const action = await webRequest<AgentAction>('video.agent', {
        question: text,
        realtime_answer: realtimeAnswer,
        current_task: currentTaskRef.current,
        recent_chat: recentChatForRouter(),
        search_session_id: searchSessionRef.current,
      }, { timeoutMs: 45_000 });
      if (version !== latestMeaningfulAgentVersionRef.current) return;
      if (action.current_task || action.tools_used?.includes('stop_current_task')) {
        applyCurrentTask(action.current_task || '');
      }
      rememberSearchJob(action.search_job);
    } catch (sendError) {
      setError(sendError instanceof Error ? sendError.message : '文字输入发送失败');
    }
  };

  return (
    <section className="video-live">
      <header className="video-live__header">
        <div className="video-live__brand">
          <span className="video-live__brand-icon"><Video aria-hidden /></span>
          <div>
            <h1>Jiuwen Full-duplex</h1>
            <p>实时多屏音视频问答</p>
          </div>
        </div>
        <div className={`video-live__status ${isPlaying ? 'is-live' : ''}`}>
          <span />
          {isPlaying ? 'STREAMING' : 'IDLE'}
        </div>
      </header>

      <div className="video-live__grid">
        <div className="video-live__viewer-card">
          <div className="video-live__viewer">
            <video
              ref={videoRef}
              className={source === 'screen' ? 'is-hidden' : ''}
              controls={source === 'file'}
              playsInline
              onPlay={() => setIsPlaying(true)}
              onPause={() => setIsPlaying(false)}
              onEnded={() => setIsPlaying(false)}
            />
            {source === 'screen' && (
              <div className={`video-live__screen-grid video-live__screen-grid--${Math.min(screens.length, 4)}`}>
                {screens.map((screen) => (
                  <div className="video-live__screen-tile" key={screen.id}>
                    <video
                      ref={(node) => {
                        if (!node) {
                          screenVideoRefs.current.delete(screen.id);
                          return;
                        }
                        screenVideoRefs.current.set(screen.id, node);
                        if (node.srcObject !== screen.stream) node.srcObject = screen.stream;
                        void node.play().catch(() => undefined);
                      }}
                      autoPlay
                      muted
                      playsInline
                    />
                    <div className="video-live__screen-label">
                      <span className="is-live" />
                      {screen.name}
                    </div>
                    <button
                      className="video-live__screen-close"
                      type="button"
                      onClick={() => removeScreen(screen.id)}
                      aria-label={`关闭${screen.name}`}
                    >
                      <X aria-hidden />
                    </button>
                  </div>
                ))}
              </div>
            )}
            {!source && (
              <div className="video-live__empty">
                <span className="video-live__empty-icon"><Video aria-hidden /></span>
                <strong>打开一个实时画面</strong>
                <p>使用摄像头、本地视频，或添加多个屏幕</p>
              </div>
            )}
            {source && source !== 'screen' && (
              <div className="video-live__source-chip">
                <span className={isPlaying ? 'is-live' : ''} />
                {sourceName}
              </div>
            )}
            {source && source !== 'screen' && (
              <button className="video-live__close" type="button" onClick={closeSource} aria-label="关闭视频">
                <X aria-hidden />
              </button>
            )}
          </div>

          <div className="video-live__source-actions">
            <button type="button" className="video-live__source-button" onClick={() => void startCamera()}>
              <Camera aria-hidden />
              摄像头
            </button>
            <label className="video-live__source-button">
              <FileVideo aria-hidden />
              本地视频
              <input type="file" accept="video/*" onChange={(event) => void openFile(event)} />
            </label>
            <button
              type="button"
              className="video-live__source-button"
              disabled={source === 'screen' && screens.length >= MAX_SCREENS}
              onClick={() => void startScreen()}
            >
              <Monitor aria-hidden />
              {source === 'screen' ? '添加屏幕' : '共享屏幕'}
            </button>
            {source && (
              <button type="button" className="video-live__source-button video-live__source-button--stop" onClick={closeSource}>
                <X aria-hidden />
                {source === 'camera' ? '停止摄像头' : source === 'screen' ? '停止全部屏幕' : '关闭视频'}
              </button>
            )}
            <span className="video-live__frame-count">
              滚动窗口：{frameCount}/{MAX_FRAMES} 帧
              {source === 'screen' ? ` · ${screens.length}/${MAX_SCREENS} 屏` : ''}
            </span>
          </div>
        </div>

        <div className="video-live__output-card">
          <div className="video-live__output-head">
            <div>
              <span className="video-live__eyebrow">VLM OUTPUT</span>
              <strong>{model}</strong>
            </div>
            <div className="video-live__metrics"><span>{isRealtimeStarting ? 'CONNECTING' : isRecording ? 'FULL-DUPLEX' : 'IDLE'}</span></div>
          </div>

          <div className="video-live__prompt-banner">
            <span>当前任务</span>
            {currentTask || (isRecording ? '持续监听中，可随时插话' : '开启 Full-duplex 后持续监听')}
          </div>

          <div className="video-live__answer">
            {chatHistory.length > 0 || streamingAnswer ? (
              <div className="video-live__chat-history" ref={chatHistoryRef}>
                {chatHistory.map((item) => (
                  <div className={`video-live__chat-item is-${item.role}`} key={item.id}>
                    <strong>{item.role === 'user' ? '你' : item.role === 'tool' ? '九问搜索' : '助手'}</strong>
                    <p>{item.role === 'tool' ? searchSummary(item.text) : item.text}</p>
                  </div>
                ))}
                {streamingAnswer && !isAwaitingVoiceTranscript && (
                  <div className="video-live__chat-item is-assistant is-streaming">
                    <strong>助手</strong>
                    <p>{streamingAnswer}</p>
                  </div>
                )}
              </div>
            ) : answer ? (
              <p>{answer}</p>
            ) : (
              <div className="video-live__answer-empty">
                <Video aria-hidden />
                <strong>{isRecording ? '正在持续听取' : '等待开启 Full-duplex'}</strong>
                <span>开启后直接说话，无需逐句点击</span>
              </div>
            )}
          </div>

          {error && <div className="video-live__error">{error}</div>}
          {realtimeStatus && <div className="video-live__realtime-status">{realtimeStatus}</div>}
          {toolStatus && <div className="video-live__tool-status">{toolStatus}</div>}

          <form className="video-live__composer" onSubmit={(event) => void sendText(event)}>
            <button
              type="button"
              className={`video-live__mic${isRecording ? ' is-recording' : ''}`}
              onClick={isRecording ? stopRealtime : () => void startRealtime()}
              disabled={isRealtimeStarting && !isRecording}
              aria-label={isRealtimeStarting ? '正在启动 Full-duplex 会话' : isRecording ? '结束 Full-duplex 会话' : '开启 Full-duplex 会话'}
              title={isRealtimeStarting ? realtimeStatus : isRecording ? '结束 Full-duplex 会话' : '开启 Full-duplex 会话'}
            >
              {isRealtimeStarting && !isRecording
                ? <LoaderCircle className="video-live__spinner" aria-hidden />
                : isRecording ? <Square aria-hidden /> : <Mic aria-hidden />}
            </button>
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={isRecording ? '也可以在当前会话中输入文字……' : '先开启 Full-duplex……'}
            />
            <button
              type="submit"
              disabled={!question.trim()}
              aria-label="发送问题"
              title="发送问题"
            >
              <Send aria-hidden />
            </button>
          </form>
        </div>
      </div>

      <canvas ref={canvasRef} className="video-live__capture-canvas" aria-hidden />
    </section>
  );
}
