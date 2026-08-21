import { webClient, webRequest } from '../../services/webClient';
import { canPlayJoyAIResponse, JoyAIVoiceSession } from '../../utils/joyaiVoice';
import { assistantSpeechText, groundedSearchAnswer } from '../../utils/searchPresentation';
import {
  AgentAction,
  JoyAIFrameResult,
  SearchJobPayload,
  SearchJobState,
  TtsStreamPayload,
} from './types';

const MONITOR_INTERVAL_MS = 1_000;
const MONITOR_CLIENT_BUILD = 'joyai-official-prompt-lifecycle-v8';

export interface JoyAIProviderCallbacks {
  getLatestFrameDataUrl: () => string;
  getFrameCount: () => number;
  getSearchSessionId: () => string;
  hasPendingTranscriptions: () => boolean;
  beginTranscription: () => void;
  finishTranscription: () => void;
  interruptAgentRequests: () => void;
  appendChat: (role: 'user' | 'assistant' | 'tool', text: string) => void;
  applyCurrentTask: (task: string) => void;
  commitAssistantAnswer: (text: string, toolJobId?: string) => void;
  rememberSearchJob: (job: AgentAction['search_job']) => void;
  updateSearchJob: (job: SearchJobState) => void;
  setAwaitingVoiceTranscript: (awaiting: boolean) => void;
  setError: (message: string) => void;
  setToolStatus: (status: string) => void;
  setRecording: (recording: boolean) => void;
  setStatus: (status: string) => void;
  setStarting: (starting: boolean) => void;
  report: (event: string, details?: Record<string, unknown>) => void;
}

interface RequestOptions {
  skipIfBusy?: boolean;
  toolJobId?: string;
  frameDataUrl?: string;
  required?: boolean;
  joyaiSessionId?: string;
  commitResponse?: boolean;
  requestKind?: 'user' | 'monitor' | 'tool';
  frameOnly?: boolean;
}

export class JoyAIProvider {
  private callbacks: JoyAIProviderCallbacks;
  private voice: JoyAIVoiceSession | null = null;
  private sessionId = '';
  private monitorTimer: number | null = null;
  private requestQueue: Promise<void> = Promise.resolve();
  private queuedRequestCount = 0;
  private sessionStartedAt = 0;
  private lastFrameTime = 0;
  private handledTurns = new Set<string>();
  private ttsGeneration = 0;
  private userSpeechActive = false;
  private userSpeechEpoch = 0;
  private ttsQueue: Promise<void> = Promise.resolve();
  private activeTtsStream = '';
  private searchDeliveryQueue: Promise<void> = Promise.resolve();

  constructor(callbacks: JoyAIProviderCallbacks) {
    this.callbacks = callbacks;
  }

  updateCallbacks(callbacks: JoyAIProviderCallbacks): void {
    this.callbacks = callbacks;
  }

  get active(): boolean {
    return Boolean(this.sessionId);
  }

  async start(): Promise<void> {
    if (this.active) return;
    const sessionId = crypto.randomUUID();
    this.sessionId = sessionId;
    this.userSpeechActive = false;
    this.userSpeechEpoch += 1;
    this.sessionStartedAt = performance.now();
    this.lastFrameTime = 0;
    this.startMonitor();

    const voice = new JoyAIVoiceSession({
      onSpeechStart: () => {
        this.userSpeechActive = true;
        this.userSpeechEpoch += 1;
        this.interruptTts();
        this.callbacks.interruptAgentRequests();
        this.callbacks.report('joyai_barge_in_started', {
          speech_epoch: this.userSpeechEpoch,
          tts_generation: this.ttsGeneration,
        });
      },
      onTurnAudio: (audioDataUrl, turnId) => {
        void this.handleTurnAudio(audioDataUrl, turnId, sessionId);
      },
      onState: (state) => {
        if (state === 'closed') {
          this.callbacks.setRecording(false);
          this.callbacks.setStatus('');
          return;
        }
        this.callbacks.setRecording(true);
        this.callbacks.setStatus(state === 'connecting'
          ? '正在申请麦克风权限…'
          : state === 'speaking'
            ? '模型正在回答…'
            : '');
        if (state === 'listening') this.callbacks.setStarting(false);
      },
      onError: this.callbacks.setError,
    });
    this.voice = voice;
    this.callbacks.setStatus('正在申请麦克风权限…');
    await voice.start();
  }

  stop(): void {
    this.ttsGeneration += 1;
    this.userSpeechActive = false;
    this.userSpeechEpoch += 1;
    const activeTtsStream = this.activeTtsStream;
    this.activeTtsStream = '';
    if (activeTtsStream) {
      void webRequest('tts.stream.cancel', { stream_id: activeTtsStream }, { timeoutMs: 5_000 })
        .catch(() => undefined);
    }
    this.voice?.stop();
    this.voice = null;
    this.ttsQueue = Promise.resolve();
    this.searchDeliveryQueue = Promise.resolve();
    this.sessionId = '';
    this.requestQueue = Promise.resolve();
    this.queuedRequestCount = 0;
    this.sessionStartedAt = 0;
    this.lastFrameTime = 0;
    this.handledTurns.clear();
    if (this.monitorTimer !== null) {
      window.clearInterval(this.monitorTimer);
      this.monitorTimer = null;
    }
  }

  async submitUserInstruction(text: string): Promise<JoyAIFrameResult | null> {
    this.callbacks.applyCurrentTask(text);
    return this.requestFrame(text, text, { requestKind: 'user' });
  }

  handleCompletedSearch(payload: SearchJobPayload, existing?: SearchJobState): boolean {
    if (!this.active || !payload.job_id) return false;
    const result = payload.result?.trim() || '';
    const question = payload.question?.trim() || existing?.question || '';
    if (!result || !question) return false;

    const sessionId = this.sessionId;
    this.callbacks.updateSearchJob({
      id: payload.job_id,
      searchSessionId: payload.search_session_id || existing?.searchSessionId || '',
      question,
      query: payload.query?.trim() || existing?.query || '',
      status: 'queued',
    });
    const groundedAnswer = groundedSearchAnswer(result);
    this.callbacks.appendChat('tool', `${payload.engine || '九问搜索 Agent'}搜索完成`);
    this.callbacks.setToolStatus('搜索完成，等待当前回答结束后展示…');
    this.callbacks.report('search_result_waiting_for_output_slot', {
      job_id: payload.job_id,
      message: 'Grounded search answer queued behind active user requests and speech',
    });

    const deliver = async () => {
      if (!await this.waitForAnswerSlot(sessionId)) return;
      this.commitAndSpeak(groundedAnswer || result, payload.job_id);
      this.callbacks.setToolStatus('');
      this.callbacks.report('search_result_answered', {
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
        await this.requestFrame(groundedInstruction, question, {
          frameDataUrl: existing?.frameDataUrl,
          commitResponse: false,
          requestKind: 'tool',
        });
        this.callbacks.report('search_result_dispatched', {
          job_id: payload.job_id,
          message: 'Grounded search context synchronized to JoyAI',
        });
      } catch (error) {
        const message = error instanceof Error ? error.message : '请重试';
        this.callbacks.report('search_result_response_empty', {
          job_id: payload.job_id,
          message: `Grounded answer displayed, but JoyAI context synchronization failed: ${message}`,
        });
      }
      await this.ttsQueue;
    };
    const queued = this.searchDeliveryQueue.then(deliver, deliver);
    this.searchDeliveryQueue = queued.then(() => undefined, () => undefined);
    return true;
  }

  private async handleTurnAudio(audioDataUrl: string, turnId: string, sessionId: string): Promise<void> {
    if (this.handledTurns.has(turnId)) return;
    this.handledTurns.add(turnId);
    while (this.handledTurns.size > 8) {
      const oldest = this.handledTurns.values().next().value;
      if (!oldest) break;
      this.handledTurns.delete(oldest);
    }
    const speechEpochAtTurn = this.userSpeechEpoch;
    let releasedForInstruction = false;
    this.callbacks.beginTranscription();
    try {
      const asr = await webRequest<{ transcript?: string }>('video.transcribe', {
        audio_data_url: audioDataUrl,
      }, { timeoutMs: 45_000 });
      if (!this.active || this.sessionId !== sessionId) return;
      const transcript = asr.transcript?.trim();
      if (!transcript) return;
      this.callbacks.appendChat('user', transcript);
      this.callbacks.setAwaitingVoiceTranscript(false);
      if (speechEpochAtTurn === this.userSpeechEpoch) {
        this.interruptTts();
        this.userSpeechActive = false;
        releasedForInstruction = true;
        this.callbacks.report('joyai_barge_in_released_for_instruction', {
          speech_epoch: speechEpochAtTurn,
          tts_generation: this.ttsGeneration,
          transcript_chars: transcript.length,
        });
      }
      await this.submitUserInstruction(transcript);
    } catch (error) {
      if (this.active && this.sessionId === sessionId) {
        this.callbacks.setError(error instanceof Error ? error.message : 'JoyAI 语音处理失败');
      }
    } finally {
      if (!releasedForInstruction && speechEpochAtTurn === this.userSpeechEpoch) {
        this.interruptTts();
        this.userSpeechActive = false;
        this.callbacks.report('joyai_barge_in_released_without_instruction', {
          speech_epoch: speechEpochAtTurn,
          tts_generation: this.ttsGeneration,
        });
      }
      this.callbacks.finishTranscription();
    }
  }

  private startMonitor(): void {
    if (this.monitorTimer !== null) return;
    this.callbacks.report('joyai_monitor_started', {
      client_build: MONITOR_CLIENT_BUILD,
      frame_count: this.callbacks.getFrameCount(),
    });
    const sendLatestFrame = () => {
      if (!this.active) return;
      void this.requestFrame('', '', {
        skipIfBusy: true,
        requestKind: 'monitor',
        frameOnly: true,
      }).catch((error) => {
        this.callbacks.setError(error instanceof Error ? error.message : 'JoyAI 监控请求失败');
      });
    };
    sendLatestFrame();
    this.monitorTimer = window.setInterval(sendLatestFrame, MONITOR_INTERVAL_MS);
  }

  private async requestFrame(
    instruction: string,
    originalQuestion = '',
    options: RequestOptions = {},
  ): Promise<JoyAIFrameResult | null> {
    if (!this.active) {
      if (options.required) throw new Error('JoyAI 会话已停止，无法回填搜索结果');
      return null;
    }
    const activeSessionId = this.sessionId;
    const sessionId = options.joyaiSessionId || activeSessionId;
    const frameDataUrl = options.frameDataUrl || this.callbacks.getLatestFrameDataUrl();
    if (!sessionId || !frameDataUrl) {
      if (options.required) throw new Error('没有可用于搜索结果回填的 JoyAI 会话画面');
      return null;
    }
    if (options.skipIfBusy && this.queuedRequestCount > 0) return null;

    const ttsGenerationAtRequest = this.ttsGeneration;
    this.queuedRequestCount += 1;
    const execute = async (): Promise<JoyAIFrameResult | null> => {
      if (!this.active || this.sessionId !== activeSessionId) {
        if (options.required) throw new Error('JoyAI 会话在搜索期间已结束或被替换');
        return null;
      }
      const requestKind = options.requestKind || (originalQuestion ? 'user' : 'monitor');
      const prompt = options.frameOnly ? '' : instruction.trim();
      const sessionElapsedSeconds = this.sessionStartedAt > 0
        ? Math.max(0, (performance.now() - this.sessionStartedAt) / 1_000)
        : 0;
      const frameRangeStart = Math.min(this.lastFrameTime, sessionElapsedSeconds);
      this.lastFrameTime = sessionElapsedSeconds;
      const frameTimeRange = `${frameRangeStart.toFixed(1)} seconds ~ ${sessionElapsedSeconds.toFixed(1)} seconds`;
      const result = await webRequest<JoyAIFrameResult>('video.joyai.frame', {
        frame_data_url: frameDataUrl,
        instruction: prompt.slice(0, 2_000),
        question: originalQuestion.slice(0, 500),
        request_kind: requestKind,
        joyai_session_id: sessionId,
        search_session_id: this.callbacks.getSearchSessionId(),
        frame_time_range: frameTimeRange,
      }, { timeoutMs: 60_000 });
      if (!this.active || this.sessionId !== activeSessionId) {
        if (options.required) throw new Error('JoyAI 会话在搜索结果返回前已结束或被替换');
        return null;
      }
      const response = result.response?.trim() || '';
      if (response && options.commitResponse !== false) {
        this.commitAndSpeak(response, options.toolJobId, ttsGenerationAtRequest);
      }
      this.callbacks.rememberSearchJob(result.search_job);
      return result;
    };
    const queuedRequest = this.requestQueue.then(execute, execute);
    this.requestQueue = queuedRequest.then(() => undefined, () => undefined);
    try {
      return await queuedRequest;
    } finally {
      this.queuedRequestCount = Math.max(0, this.queuedRequestCount - 1);
    }
  }

  private commitAndSpeak(text: string, toolJobId?: string, generation = this.ttsGeneration): void {
    this.callbacks.commitAssistantAnswer(text, toolJobId);
    if (!canPlayJoyAIResponse(generation, this.ttsGeneration, this.userSpeechActive)) {
      this.callbacks.report('joyai_tts_suppressed_for_barge_in', {
        user_speech_active: this.userSpeechActive,
        response_generation: generation,
        current_generation: this.ttsGeneration,
        text_chars: text.trim().length,
      });
      return;
    }
    this.speakText(text, generation);
  }

  private interruptTts(): void {
    this.ttsGeneration += 1;
    const activeTtsStream = this.activeTtsStream;
    this.activeTtsStream = '';
    this.voice?.interruptPlayback();
    this.ttsQueue = Promise.resolve();
    if (activeTtsStream) {
      void webRequest('tts.stream.cancel', { stream_id: activeTtsStream }, { timeoutMs: 5_000 })
        .catch(() => undefined);
    }
  }

  private speakText(text: string, generation: number): void {
    const voice = this.voice;
    const spokenText = assistantSpeechText(text);
    if (!this.active || !voice || !spokenText
      || !canPlayJoyAIResponse(generation, this.ttsGeneration, this.userSpeechActive)) return;

    const play = async () => {
      if (!canPlayJoyAIResponse(generation, this.ttsGeneration, this.userSpeechActive)) return;
      const streamId = typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `tts-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      let receivedStreamAudio = false;
      let streamStarted = false;
      const playback = voice.beginPcmStream(streamId);
      this.activeTtsStream = streamId;
      const unsubscribeChunk = webClient.on<TtsStreamPayload>('video.tts.chunk', ({ payload }) => {
        if (payload.stream_id !== streamId
          || !canPlayJoyAIResponse(generation, this.ttsGeneration, this.userSpeechActive)) return;
        const audioBase64 = payload.audio_base64 || '';
        if (!audioBase64) return;
        receivedStreamAudio = true;
        try {
          voice.appendPcm16Chunk(streamId, audioBase64, payload.sample_rate || 24_000);
        } catch (error) {
          voice.failPcmStream(
            streamId,
            error instanceof Error ? error.message : 'JoyAI 音频分块解析失败',
          );
        }
      });
      const unsubscribeDone = webClient.on<TtsStreamPayload>('video.tts.done', ({ payload }) => {
        if (payload.stream_id === streamId) voice.finishPcmStream(streamId);
      });
      const unsubscribeCancelled = webClient.on<TtsStreamPayload>(
        'video.tts.cancelled',
        ({ payload }) => {
          if (payload.stream_id === streamId) voice.interruptPlayback();
        },
      );
      const unsubscribeError = webClient.on<TtsStreamPayload>('video.tts.error', ({ payload }) => {
        if (payload.stream_id === streamId) {
          voice.failPcmStream(streamId, payload.error || 'JoyAI 语音流失败');
        }
      });
      try {
        this.callbacks.report('realtime_tts_synthesis_started', {
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
        if (!this.active
          || !canPlayJoyAIResponse(generation, this.ttsGeneration, this.userSpeechActive)) return;
        this.callbacks.report('realtime_tts_playback_completed', {
          text_chars: spokenText.length,
          stream_id: streamId,
          streamed: true,
        });
      } catch (caughtError) {
        let ttsError: unknown = caughtError;
        if (!this.active
          || !canPlayJoyAIResponse(generation, this.ttsGeneration, this.userSpeechActive)) return;
        voice.interruptPlayback();
        if (!streamStarted && !receivedStreamAudio) {
          try {
            const result = await webRequest<{ audio_base64?: string; audio_mime?: string }>(
              'tts.synthesize',
              { text: spokenText },
              { timeoutMs: 60_000 },
            );
            if (!this.active || this.voice !== voice || !result.audio_base64
              || !canPlayJoyAIResponse(generation, this.ttsGeneration, this.userSpeechActive)) return;
            await voice.speak(`data:${result.audio_mime || 'audio/wav'};base64,${result.audio_base64}`);
            this.callbacks.report('realtime_tts_playback_completed', {
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
        this.callbacks.report('realtime_tts_failed', {
          text_chars: spokenText.length,
          stream_id: streamId,
          message,
        });
        this.callbacks.setError(message);
      } finally {
        unsubscribeChunk();
        unsubscribeDone();
        unsubscribeCancelled();
        unsubscribeError();
        if (this.activeTtsStream === streamId) this.activeTtsStream = '';
      }
    };
    const queued = this.ttsQueue.then(play, play);
    this.ttsQueue = queued.then(() => undefined, () => undefined);
  }

  private async waitForAnswerSlot(sessionId: string): Promise<boolean> {
    while (this.active && this.sessionId === sessionId) {
      const requestBarrier = this.requestQueue;
      await requestBarrier;
      const ttsBarrier = this.ttsQueue;
      await ttsBarrier;
      if (!this.callbacks.hasPendingTranscriptions()
        && !this.userSpeechActive
        && this.queuedRequestCount === 0
        && requestBarrier === this.requestQueue
        && ttsBarrier === this.ttsQueue) return true;
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    }
    return false;
  }
}
