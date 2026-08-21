import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { Camera, FileVideo, LoaderCircle, Mic, Monitor, Send, Square, Video, X } from 'lucide-react';
import { webClient, webRequest } from '../../services/webClient';
import {
  advanceMeaningfulVideoAgentVersion,
  collectVideoAgentTurns,
  VideoAgentSegment,
} from '../../utils/videoAgentSegments';
import { JoyAIProvider } from './joyaiProvider';
import { createRealtimeProvider, RealtimeDuplexSession } from './realtimeProvider';
import {
  AgentAction,
  ChatContextItem,
  SearchJobPayload,
  SearchJobState,
  VideoSessionConfig,
} from './types';
import './VideoLivePanel.css';

type VideoSource = 'camera' | 'file' | 'screen' | null;
interface CapturedFrame {
  data_url: string;
  source_id: string;
}

interface ScreenSource {
  id: string;
  name: string;
  stream: MediaStream;
}

const FRAME_INTERVAL_MS = 500;
const MAX_FRAMES = 6;
const MAX_SCREENS = 4;
const MAX_FRAME_WIDTH = 1024;
const SCREEN_PREVIEW_FRAME_RATE = 30;
const FRAME_JPEG_QUALITY = 0.8;
const AGENT_DEBOUNCE_MS = 700;
const FIRST_FRAME_WAIT_MS = 3_000;

function cleanAssistantText(text: string): string {
  return text
    .replace(/<think>[\s\S]*?<\/think>/gi, '')
    .replace(/<\/?think>/gi, '')
    .trim();
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
  const joyaiProviderRef = useRef<JoyAIProvider | null>(null);
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
    joyaiProviderRef.current?.stop();
  };

  const resetVisualContext = () => {
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

  const commitAssistantAnswer = (
    text: string,
    toolJobId?: string,
    turnId?: string,
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

  const getJoyAIProvider = (): JoyAIProvider => {
    const callbacks = {
      getLatestFrameDataUrl: () => framesRef.current.at(-1)?.data_url || '',
      getFrameCount: () => framesRef.current.length,
      getSearchSessionId: () => searchSessionRef.current,
      hasPendingTranscriptions: () => pendingTranscriptionsRef.current > 0,
      beginTranscription: () => {
        pendingTranscriptionsRef.current += 1;
        setIsAwaitingVoiceTranscript(true);
      },
      finishTranscription: () => {
        pendingTranscriptionsRef.current = Math.max(0, pendingTranscriptionsRef.current - 1);
        if (pendingTranscriptionsRef.current === 0) {
          setIsAwaitingVoiceTranscript(false);
          flushDeferredAssistantAnswers();
        }
      },
      interruptAgentRequests: () => {
        agentRequestVersionRef.current += 1;
      },
      appendChat,
      applyCurrentTask,
      commitAssistantAnswer: (text: string, toolJobId?: string) => {
        commitAssistantAnswer(text, toolJobId);
      },
      rememberSearchJob,
      updateSearchJob: (job: SearchJobState) => {
        searchJobsRef.current.set(job.id, job);
      },
      setAwaitingVoiceTranscript: setIsAwaitingVoiceTranscript,
      setError,
      setToolStatus,
      setRecording: setIsRecording,
      setStatus: setRealtimeStatus,
      setStarting: setIsRealtimeStarting,
      report: reportRealtimeEvent,
    };
    if (!joyaiProviderRef.current) {
      joyaiProviderRef.current = new JoyAIProvider(callbacks);
    } else {
      joyaiProviderRef.current.updateCallbacks(callbacks);
    }
    return joyaiProviderRef.current;
  };

  const recentChatForRouter = () => recentChatRef.current
    .slice(-8)
    .map((item) => `${item.role === 'user' ? '用户' : item.role === 'assistant' ? 'Realtime助手' : '工具'}：${item.text}`)
    .join('\n');

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
    if (getJoyAIProvider().handleCompletedSearch(payload, existing)) return;
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
      setToolStatus(`${payload.engine || '九问搜索 Agent'}完成，等待模型空闲后回答…`);
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
    setToolStatus(`${payload.engine || '九问搜索 Agent'}失败：${payload.error || '请重试'}`);
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
      setToolStatus(`正在使用${payload.engine || '九问搜索 Agent'}，可继续提问…`);
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
            const frame = await captureVideo(video, screen.id);
            if (frame) captured.push(frame);
          }
        } else {
          const video = videoRef.current;
          if (video) {
            const frame = await captureVideo(video, source || 'video');
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
  }, [isPlaying, screens, source]);

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
    if (duplexRef.current || joyaiProviderRef.current?.active) return;
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
        await getJoyAIProvider().start();
        return;
      }
      const session = createRealtimeProvider(config, {
        getVideoFrame: () => framesRef.current.at(-1)?.data_url.split(',', 2)[1] || null,
        onAssistantText: (text, final, toolJobId, turnId) => {
          const visibleText = cleanAssistantText(text);
          if (!visibleText) return;
          if (!final) {
            streamingAnswerRef.current = visibleText;
            streamingToolJobIdRef.current = toolJobId;
            streamingAnswerTurnIdRef.current = turnId;
            setStreamingAnswer(visibleText);
          } else {
            commitAssistantAnswer(visibleText, toolJobId, turnId);
          }
        },
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
      }, () => reportRealtimeEvent('realtime_start_unsupported_browser'));
      duplexRef.current = session;
      session.updateContext(currentTaskRef.current, recentChatRef.current);
      await session.start();
    } catch (realtimeError) {
      const wasJoyAI = Boolean(joyaiProviderRef.current?.active);
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
    const wasJoyAI = Boolean(joyaiProviderRef.current?.active);
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
    if (!duplexRef.current && !joyaiProviderRef.current?.active) await startRealtime();
    if (!duplexRef.current && !joyaiProviderRef.current?.active) return;
    const version = ++agentRequestVersionRef.current;
    latestMeaningfulAgentVersionRef.current = version;
    try {
      appendChat('user', text);
      setQuestion('');
      if (joyaiProviderRef.current?.active) {
        const result = await getJoyAIProvider().submitUserInstruction(text);
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
                    <p>{item.role === 'tool' ? '九问搜索 Agent 搜索完成' : item.text}</p>
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
