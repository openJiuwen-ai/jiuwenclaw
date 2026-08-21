import { ChangeEvent, FormEvent, useCallback, useEffect, useRef, useState } from 'react';
import { Camera, FileVideo, Mic, Monitor, Pause, Send, Square, Video, X } from 'lucide-react';
import { webClient, webRequest } from '../../services/webClient';
import { RealtimeDuplexSession } from '../../utils/realtimeDuplex';
import { RealtimeVideoFrameScheduler } from '../../utils/realtimeVideoFrameScheduler';
import {
  isVideoSourceReady,
  RealtimeVideoSource,
  waitForFirstVideoFrame,
} from '../../utils/realtimeVideoSourceReadiness';
import './VideoLivePanel.css';

type VideoSource = RealtimeVideoSource;
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

interface AgentProgress {
  client_token?: string;
  stage?: 'started' | 'completed' | 'failed';
  engine?: string;
}

const FRAME_INTERVAL_MS = 1_000;
const MAX_FRAMES = 6;
const MAX_SCREENS = 4;

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

function isUsefulTranscript(text: string): boolean {
  return text.replace(/[^\p{L}\p{N}]/gu, '').length >= 2;
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
  const answerRef = useRef('');
  const currentTaskRef = useRef('');
  const recentChatRef = useRef<ChatContextItem[]>([]);
  const displayChatRef = useRef<ChatContextItem[]>([]);
  const displayTurnOrderRef = useRef<Map<string, number>>(new Map());
  const startingRealtimeRef = useRef<Promise<void> | null>(null);
  const interactionEpochRef = useRef(0);
  const agentProgressTokenRef = useRef('');
  const chatSequenceRef = useRef(0);
  const pendingAssistantRef = useRef<string[]>([]);
  const streamingAnswerRef = useRef('');
  const suppressAssistantRef = useRef(false);
  const agentRoutingRef = useRef(false);

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
  const [currentTask, setCurrentTask] = useState('');

  const resetVisualContext = () => {
    answerRef.current = '';
    interactionEpochRef.current += 1;
    agentProgressTokenRef.current = '';
    chatSequenceRef.current = 0;
    pendingAssistantRef.current = [];
    streamingAnswerRef.current = '';
    suppressAssistantRef.current = false;
    agentRoutingRef.current = false;
    currentTaskRef.current = '';
    recentChatRef.current = [];
    displayChatRef.current = [];
    displayTurnOrderRef.current.clear();
    setAnswer('');
    setChatHistory([]);
    setStreamingAnswer('');
    setCurrentTask('');
    setToolStatus('');
  };

  const appendChat = (role: ChatContextItem['role'], text: string, includeInContext = true) => {
    const normalized = text.trim();
    if (!normalized) return;
    const previous = displayChatRef.current.at(-1);
    if (previous?.role === role && previous.text === normalized) return;
    const item = { id: ++chatSequenceRef.current, role, text: normalized };
    displayChatRef.current = [...displayChatRef.current, item].slice(-12);
    setChatHistory(displayChatRef.current);
    if (includeInContext) recentChatRef.current = [...recentChatRef.current, item].slice(-12);
  };

  const insertDisplayTranscript = (text: string, id: number) => {
    const normalized = text.trim();
    if (!isUsefulTranscript(normalized)) return;
    const matching = displayChatRef.current.filter((item) => item.role === 'user'
      && Math.abs(item.id - id) === 1
      && item.text !== normalized
      && (normalized.includes(item.text) || item.text.includes(normalized)));
    const effectiveId = matching.reduce((earliest, item) => Math.min(earliest, item.id), id);
    const longestText = matching.reduce(
      (longest, item) => item.text.length > longest.length ? item.text : longest,
      normalized,
    );
    const matchingIds = new Set(matching.map((item) => item.id));
    const item: ChatContextItem = { id: effectiveId, role: 'user', text: longestText };
    displayChatRef.current = [
      ...displayChatRef.current.filter((existing) => !matchingIds.has(existing.id)),
      item,
    ].sort((left, right) => left.id - right.id).slice(-12);
    setChatHistory(displayChatRef.current);
  };

  const applyCurrentTask = (task: string) => {
    const normalized = task.trim();
    if (!normalized || normalized === currentTaskRef.current) return;
    currentTaskRef.current = normalized;
    setCurrentTask(normalized);
    duplexRef.current?.updateContext(normalized, recentChatRef.current);
  };

  const flushPendingAssistant = () => {
    const messages = pendingAssistantRef.current;
    pendingAssistantRef.current = [];
    messages.forEach((message) => appendChat('assistant', message));
  };

  const returnToolResultToRealtime = async (toolResult: string, interactionEpoch: number) => {
    const normalized = toolResult.trim();
    const duplex = duplexRef.current;
    const isFresh = () => interactionEpoch === interactionEpochRef.current;
    if (!normalized || !duplex || !isFresh()) return false;
    if (streamingAnswerRef.current) appendChat('assistant', streamingAnswerRef.current);
    streamingAnswerRef.current = '';
    setStreamingAnswer('');
    appendChat('tool', normalized);
    setToolStatus('');
    const sent = await duplex.sendTextTurn(`九问搜索结果如下：${normalized}\n请根据这个结果直接回答用户的问题。`, isFresh);
    suppressAssistantRef.current = false;
    return sent;
  };

  useEffect(() => webClient.on<AgentProgress>('video.agent.progress', ({ payload }) => {
    if (!payload.client_token || payload.client_token !== agentProgressTokenRef.current) return;
    if (payload.stage === 'started') {
      setToolStatus(`正在使用${payload.engine || '九问免费搜索'}…`);
      suppressAssistantRef.current = true;
      pendingAssistantRef.current = [];
      duplexRef.current?.holdForTool();
    } else if (payload.stage === 'completed') {
      setToolStatus(`${payload.engine || '免费搜索'}搜索完成`);
    } else if (payload.stage === 'failed') {
      setToolStatus(`${payload.engine || '九问免费搜索'}失败，请重试`);
      suppressAssistantRef.current = false;
    }
  }), []);

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
    duplexRef.current?.stop();
    duplexRef.current = null;
    setIsRecording(false);
    releaseScreens();
    releaseSource();
    setSource(null);
    setSourceName('');
    resetVisualContext();
    setError('');
  };

  useEffect(() => () => {
    duplexRef.current?.stop();
    duplexRef.current = null;
    releaseScreens(false);
    releaseSource();
  }, [releaseScreens, releaseSource]);

  useEffect(() => {
    if (!isPlaying) return;

    const captureVideo = (video: HTMLVideoElement, sourceId: string, sourceLabel: string) => {
      const canvas = canvasRef.current;
      if (!canvas || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) return;
      if (!video.videoWidth || !video.videoHeight) return;

      canvas.width = video.videoWidth;
      canvas.height = video.videoHeight;
      const context = canvas.getContext('2d');
      if (!context) return;

      context.drawImage(video, 0, 0, canvas.width, canvas.height);
      const frame = {
        data_url: canvas.toDataURL('image/jpeg', 0.7),
        source_id: sourceId,
        source_label: sourceLabel,
      };
      framesRef.current.push(frame);
    };

    const capture = () => {
      if (source === 'screen') {
        screens.forEach((screen) => {
          const video = screenVideoRefs.current.get(screen.id);
          if (video) captureVideo(video, screen.id, screen.name);
        });
      } else {
        const video = videoRef.current;
        if (video) {
          captureVideo(
            video,
            source || 'video',
            source === 'camera' ? '摄像头' : sourceName || '本地视频',
          );
        }
      }
      if (framesRef.current.length > MAX_FRAMES) {
        framesRef.current.splice(0, framesRef.current.length - MAX_FRAMES);
      }
      setFrameCount(framesRef.current.length);
    };

    capture();
    const timer = window.setInterval(capture, FRAME_INTERVAL_MS);
    return () => window.clearInterval(timer);
  }, [isPlaying, screens, source, sourceName]);

  const startCamera = async () => {
    duplexRef.current?.stop();
    duplexRef.current = null;
    setIsRecording(false);
    releaseScreens();
    releaseSource();
    setSource(null);
    setSourceName('');
    resetVisualContext();
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        video: true,
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

    duplexRef.current?.stop();
    duplexRef.current = null;
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
        video: { frameRate: { ideal: 2, max: 5 } },
        audio: false,
      });
      const track = stream.getVideoTracks()[0];
      if (!track) {
        stream.getTracks().forEach((item) => item.stop());
        throw new Error('没有获得屏幕画面。');
      }

      if (source !== 'screen') {
        duplexRef.current?.stop();
        duplexRef.current = null;
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

  const handleFinalUserText = (text: string, visualAnswerAtTurn = answerRef.current) => {
    const transcript = text.trim();
    if (!isUsefulTranscript(transcript)) return;
    const interactionEpoch = ++interactionEpochRef.current;
    agentProgressTokenRef.current = '';
    suppressAssistantRef.current = false;
    pendingAssistantRef.current = [];
    streamingAnswerRef.current = '';
    setStreamingAnswer('');
    setToolStatus('');
    appendChat('user', transcript);
    agentRoutingRef.current = true;
    const progressToken = crypto.randomUUID();
    agentProgressTokenRef.current = progressToken;
    void (async () => {
      try {
        const action = await webRequest<{
          answer?: string;
          current_task?: string;
          tools_used?: string[];
        }>('video.agent', {
          question: transcript,
          visual_answer: visualAnswerAtTurn,
          current_task: currentTaskRef.current,
          client_token: progressToken,
        }, { timeoutMs: 45_000 });
        if (interactionEpoch !== interactionEpochRef.current) return;
        agentRoutingRef.current = false;
        if (action.current_task) applyCurrentTask(action.current_task);
        if (action.tools_used?.includes('mcp_free_search') && action.answer) {
          pendingAssistantRef.current = [];
          const sent = await returnToolResultToRealtime(action.answer, interactionEpoch);
          if (!sent && interactionEpoch === interactionEpochRef.current) {
            setToolStatus('九问结果回填失败，请重试');
          }
        } else {
          setToolStatus('');
          flushPendingAssistant();
        }
      } catch {
        agentRoutingRef.current = false;
        setToolStatus('九问搜索失败，请重试');
        suppressAssistantRef.current = false;
        flushPendingAssistant();
      }
    })();
  };

  const startRealtime = async () => {
    if (duplexRef.current) return;
    if (startingRealtimeRef.current) return startingRealtimeRef.current;
    const start = (async () => {
      if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
        setError('当前浏览器不支持 Realtime 音频。');
        return;
      }
      try {
        if (source) {
          setError('正在等待首帧画面…');
          const sourceReady = () => isVideoSourceReady({
            source,
            cameraStream: cameraStreamRef.current,
            screens,
            screenStreams: screenStreamsRef.current,
            video: videoRef.current,
          });
          const ready = await waitForFirstVideoFrame(
            sourceReady,
            () => framesRef.current.length > 0,
          );
          if (!ready) throw new Error('画面尚未就绪，请等待预览出现后再开启 Realtime。');
        }
        setError('');
        const config = await webRequest<{ url: string; model: string; ref_audio_base64?: string }>('video.realtime.config', {});
        if (!config.ref_audio_base64) throw new Error('请配置 Realtime 参考音频');
        setModel(config.model);
        const videoFrames = new RealtimeVideoFrameScheduler(FRAME_INTERVAL_MS);
        const session = new RealtimeDuplexSession({
          ...config,
          refAudio: `data:audio/wav;base64,${config.ref_audio_base64}`,
        }, {
          getVideoFrame: () => {
            const frame = videoFrames.take(framesRef.current);
            if (!frame) return null;
            return frame.data_url.split(',', 2)[1] || null;
          },
          onAssistantText: (text, final) => {
            const visibleText = cleanAssistantText(text);
            if (!visibleText) return;
            answerRef.current = visibleText;
            if (!final) {
              streamingAnswerRef.current = visibleText;
              setStreamingAnswer(visibleText);
            } else {
              if (suppressAssistantRef.current) {
                streamingAnswerRef.current = visibleText;
                setStreamingAnswer(visibleText);
                return;
              }
              streamingAnswerRef.current = '';
              setStreamingAnswer('');
              if (agentRoutingRef.current) {
                pendingAssistantRef.current.push(visibleText);
              } else {
                appendChat('assistant', visibleText);
              }
              setAnswer(visibleText);
              if (agentProgressTokenRef.current) {
                agentProgressTokenRef.current = '';
                setToolStatus('');
              }
            }
          },
          onUserText: (text, final) => {
            if (!final) return;
            handleFinalUserText(text);
          },
          onUserTurnStarted: (turnId) => {
            displayTurnOrderRef.current.set(turnId, ++chatSequenceRef.current);
          },
          onUserTurnAudio: (audioDataUrl, turnId) => {
            const displayOrder = displayTurnOrderRef.current.get(turnId) || ++chatSequenceRef.current;
            displayTurnOrderRef.current.delete(turnId);
            void (async () => {
              try {
                const asr = await webRequest<{ transcript?: string }>('video.transcribe', {
                  audio_data_url: audioDataUrl,
                }, { timeoutMs: 45_000 });
                if (duplexRef.current !== session) return;
                const transcript = asr.transcript?.trim() || '';
                insertDisplayTranscript(transcript, displayOrder);
              } catch { /* Realtime answer remains usable when optional transcript fails. */ }
            })();
          },
          onState: (state) => {
            setIsRecording(state !== 'closed');
            if (state === 'speaking') setAnswer((current) => current || '正在回答…');
          },
          onError: setError,
        });
        duplexRef.current = session;
        session.updateContext(currentTaskRef.current, recentChatRef.current);
        await session.start();
      } catch (realtimeError) {
        duplexRef.current?.stop();
        duplexRef.current = null;
        setIsRecording(false);
        setError(realtimeError instanceof Error ? realtimeError.message : 'Realtime 会话启动失败。');
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
    duplexRef.current?.stop();
    duplexRef.current = null;
    setIsRecording(false);
  };

  const sendText = async (event: FormEvent) => {
    event.preventDefault();
    const text = question.trim();
    if (!text) return;
    if (!duplexRef.current) await startRealtime();
    if (!duplexRef.current) return;
    try {
      const interactionEpoch = ++interactionEpochRef.current;
      const visualAnswerAtTurn = answerRef.current;
      appendChat('user', text);
      agentRoutingRef.current = true;
      await duplexRef.current?.sendTextTurn(text);
      const progressToken = crypto.randomUUID();
      agentProgressTokenRef.current = progressToken;
      const action = await webRequest<{
        answer?: string;
        current_task?: string;
        tools_used?: string[];
      }>('video.agent', {
        question: text,
        visual_answer: visualAnswerAtTurn,
        current_task: currentTaskRef.current,
        client_token: progressToken,
      }, { timeoutMs: 45_000 });
      if (action.current_task) {
        applyCurrentTask(action.current_task);
      }
      agentRoutingRef.current = false;
      if (action.tools_used?.includes('mcp_free_search') && action.answer) {
        const sent = await returnToolResultToRealtime(action.answer, interactionEpoch);
        if (!sent && interactionEpoch === interactionEpochRef.current) {
          setToolStatus('九问结果回填失败，请重试');
        }
      } else {
        setToolStatus('');
        flushPendingAssistant();
      }
      setQuestion('');
    } catch (sendError) {
      agentRoutingRef.current = false;
      suppressAssistantRef.current = false;
      flushPendingAssistant();
      setToolStatus('');
      setError(sendError instanceof Error ? sendError.message : '文字输入发送失败');
    }
  };

  return (
    <section className="video-live">
      <header className="video-live__header">
        <div className="video-live__brand">
          <span className="video-live__brand-icon"><Video aria-hidden /></span>
          <div>
            <h1>Full Duplex</h1>
            <p>实时多屏音视频问答</p>
          </div>
        </div>
        <div className={`video-live__status ${isRecording ? 'is-live' : ''}`}>
          <span />
          {isRecording ? (source ? 'STREAMING' : 'VOICE') : 'IDLE'}
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
                <span className="video-live__empty-icon"><Mic aria-hidden /></span>
                <strong>{isRecording ? '纯语音通话中' : '可直接开始纯语音'}</strong>
                <p>摄像头为推荐场景，也可使用本地视频或多个屏幕</p>
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
            <button
              type="button"
              className={`video-live__source-button${isRecording && !source ? ' video-live__source-button--active' : ''}`}
              disabled={isRecording && Boolean(source)}
              onClick={isRecording && !source ? stopRealtime : () => void startRealtime()}
            >
              <Mic aria-hidden />
              {isRecording && !source ? '结束语音' : '纯语音'}
            </button>
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
              {source ? `画面缓存：${frameCount}/${MAX_FRAMES} 帧` : '纯语音不发送画面'}
              {source === 'screen' ? ` · ${screens.length}/${MAX_SCREENS} 屏轮询` : ''}
            </span>
          </div>
        </div>

        <div className="video-live__output-card">
          <div className="video-live__output-head">
            <div>
              <span className="video-live__eyebrow">VLM OUTPUT</span>
              <strong>{model}</strong>
            </div>
            <div className="video-live__metrics"><span>{isRecording ? 'REALTIME' : 'IDLE'}</span></div>
          </div>

          <div className="video-live__prompt-banner">
            <span>当前任务</span>
            {currentTask || (isRecording ? '持续监听中，可随时插话' : '开启 Realtime 后持续监听')}
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
                {streamingAnswer && (
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
                <strong>{isRecording ? '正在持续听取' : '等待开启 Realtime'}</strong>
                <span>开启后直接说话，无需逐句点击</span>
              </div>
            )}
          </div>

          {error && <div className="video-live__error">{error}</div>}
          {toolStatus && <div className="video-live__tool-status">{toolStatus}</div>}

          <form className="video-live__composer" onSubmit={(event) => void sendText(event)}>
            <button
              type="button"
              className={`video-live__mic${isRecording ? ' is-recording' : ''}`}
              onClick={isRecording ? stopRealtime : () => void startRealtime()}
              aria-label={isRecording ? '结束 Realtime 会话' : '开启 Realtime 会话'}
              title={isRecording ? '结束 Realtime 会话' : '开启 Realtime 会话'}
            >
              {isRecording ? <Square aria-hidden /> : <Mic aria-hidden />}
            </button>
            {isRecording && (
              <button
                type="button"
                className="video-live__interrupt"
                onClick={() => duplexRef.current?.interrupt()}
                aria-label="打断当前回答"
                title="打断当前回答"
              >
                <Pause aria-hidden />
              </button>
            )}
            <input
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={isRecording ? '也可以在当前会话中输入文字……' : '开启纯语音或音视频 Realtime……'}
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
