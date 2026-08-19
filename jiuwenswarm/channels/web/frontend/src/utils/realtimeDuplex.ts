export interface RealtimeDuplexConfig {
  url: string;
  model: string;
  refAudio: string;
}

export interface RealtimeToolResult {
  jobId: string;
  question: string;
  result: string;
}

export interface RealtimeDuplexCallbacks {
  getVideoFrame: () => string | null;
  onAssistantText: (text: string, final: boolean, toolJobId?: string, turnId?: string) => void;
  onUserText: (text: string, final: boolean) => void;
  onUserActivity: () => void;
  onTurnAudio: (wavDataUrl: string, turnId: string) => void;
  onState: (state: 'connecting' | 'listening' | 'speaking' | 'closed') => void;
  onError: (message: string) => void;
  onDiagnostic?: (event: Record<string, unknown>) => void;
  onToolResultDispatched?: (jobId: string) => void;
}

const INPUT_RATE = 16_000;
const OUTPUT_RATE = 24_000;
const SEND_INTERVAL_MS = 1_000;
const USER_TURN_SILENCE_MS = 900;
const ACTIVE_TASK_REMINDER_INTERVAL_MS = 5_000;
const REALTIME_CLIENT_BUILD = 'openai-realtime-protocol-v9';
const LISTENING_SPEECH_MS = 120;
const BARGE_IN_SPEECH_MS = 320;
const BARGE_IN_SILENCE_TOLERANCE_MS = 220;
type PlaybackLane = 'normal' | 'urgent';
const BASE_INSTRUCTIONS = [
  '你是九问实时视觉助手。',
  '始终结合当前会话中的近期聊天、近期画面和最新画面回答；最新画面优先，不得把已经消失的物体当成仍在画面中。',
  '只把当前可见画面、当前可辨语音、用户明确提供的信息和九问工具结果作为事实依据。画面模糊、文字不完整、对象无法确认时，明确说明无法确认或请用户调整画面，不得猜测品牌、文字、人物、数量或状态。',
  '天气、新闻、价格、公司背景、人物资料、地点信息及其他需要外部知识或时效性的事实，在收到[异步工具结果]前不得给出实质结论；只能简短说明正在查询。不要依据模型记忆生成一个听起来合理的答案。',
  '收到九问检索摘要后，只回答摘要正文能够直接支持的内容。摘要表示材料不足、存在冲突或无法确认时，必须保留该不确定性，不得自行补齐结论。',
  '当前任务是需要持续执行的视觉任务。任务不为“无”时，持续观察画面、维护进度，并仅在任务规定的时机主动说话；没有新进展时保持倾听。',
  '所有当前任务提醒都按紧急事件处理：条件满足时只输出一句独立提醒，不要夹带正在处理的普通对话或搜索回答。',
  '不要因为每帧画面而重复回答。持续出现的同一事件只介入一次；消失后再次出现视为新事件，可以再次介入。一个动作只在完整周期结束后计数。',
  '用户可以随时询问进度、修改、暂停或取消当前任务。回答使用自然、简洁的中文。',
  '用户提出新任务时只确认开始观察，不得把任务描述中的目标当成已经发生；只有目标在[当前任务]中且最新画面确认满足时才提醒。',
  '简单询问当前画面中清晰可见的物体或品牌是什么时可直接识别回答；用户询问公司介绍、背景资料、天气、新闻、价格或其他外部事实时，只简短说“我帮你查一下”，系统会接续九问搜索结果。',
  '用户要求持续观察、搜索外部信息或停止当前任务时，使用自然语言明确说明你理解的操作和对象；不要输出JSON、工具标签或内部控制格式。',
].join('\n');

function readableError(value: unknown): string {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object') {
    const error = value as Record<string, unknown>;
    const message = error.message || error.detail || error.code || error.error;
    if (message) return readableError(message);
    try { return JSON.stringify(value); } catch { return 'Realtime 服务错误'; }
  }
  return String(value || 'Realtime 服务错误');
}

function bytesToBase64(bytes: Uint8Array): string {
  let binary = '';
  for (let offset = 0; offset < bytes.length; offset += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
  }
  return btoa(binary);
}

function base64ToBytes(encoded: string): Uint8Array {
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes;
}

function resample(input: Int16Array, sourceRate: number, targetRate: number): Int16Array {
  if (sourceRate === targetRate) return input;
  const ratio = sourceRate / targetRate;
  const output = new Int16Array(Math.floor(input.length / ratio));
  for (let index = 0; index < output.length; index += 1) {
    const start = Math.floor(index * ratio);
    const end = Math.max(start + 1, Math.min(input.length, Math.floor((index + 1) * ratio)));
    let sum = 0;
    for (let source = start; source < end; source += 1) sum += input[source];
    output[index] = sum / (end - start);
  }
  return output;
}

export class RealtimeDuplexSession {
  private socket: WebSocket | null = null;
  private microphone: MediaStream | null = null;
  private captureContext: AudioContext | null = null;
  private playbackContext: AudioContext | null = null;
  private captureNode: AudioWorkletNode | null = null;
  private playbackNode: AudioWorkletNode | null = null;
  private pending: Int16Array[] = [];
  private pendingSamples = 0;
  private sendTimer: number | null = null;
  private lastVoiceAt = 0;
  private hasActiveTask = false;
  private activeTask = '';
  private lastTaskReminderAt = 0;
  private contextInstructions = `${BASE_INSTRUCTIONS}\n\n[当前任务]\n无`;
  private lastSentContext = '';
  private sessionReady = false;
  private responseId: string | null = null;
  private cancelledResponseIds = new Set<string>();
  private injecting = false;
  private injectionQueue: Promise<void> = Promise.resolve();
  private pendingTextInputs: Array<{ text: string; turnId: string; isFresh: () => boolean }> = [];
  private pendingToolResults: RealtimeToolResult[] = [];
  private acceptedToolResultIds = new Set<string>();
  private activeToolJobId: string | null = null;
  private lastInteractiveInputAt = 0;
  private turnAudio: Int16Array[] = [];
  private turnSamples = 0;
  private assistantPlaying = false;
  private responseActive = false;
  private discardOfficialResponseUntilListen = false;
  private playbackEpoch = 0;
  private noiseFloor = 120;
  private userSpeechMs = 0;
  private userSilenceMs = 0;
  private userActivityActive = false;
  private turnHasUserActivity = false;
  private assistantTranscript = '';
  private pendingUserTurnId: string | null = null;
  private activeAssistantTurnId: string | null = null;
  private turnSequence = 0;
  private pendingResponseLane: PlaybackLane | null = null;
  private activeResponseLane: PlaybackLane = 'normal';
  private responseLanes = new Map<string, PlaybackLane>();
  private bargeInCandidate = false;
  private bargeInPeakLevel = 0;
  private bargeInThreshold = 0;

  constructor(
    private readonly config: RealtimeDuplexConfig,
    private readonly callbacks: RealtimeDuplexCallbacks,
  ) {}

  updateContext(currentTask: string, recentChat: ReadonlyArray<{ role: string; text: string }>): void {
    const normalizedTask = currentTask.trim();
    const taskChanged = normalizedTask !== this.activeTask;
    if (taskChanged) this.lastTaskReminderAt = 0;
    this.activeTask = normalizedTask;
    this.hasActiveTask = Boolean(normalizedTask);
    const chat = recentChat
      .slice(-8)
      .map((item) => {
        const speaker = item.role === 'assistant' ? '助手' : item.role === 'tool' ? '九问工具结果' : '用户';
        return `${speaker}：${item.text.trim()}`;
      })
      .filter((line) => !line.endsWith('：'))
      .join('\n');
    this.contextInstructions = [
      BASE_INSTRUCTIONS,
      `[当前任务]\n${normalizedTask || '无'}`,
      `[当前聊天]\n${chat || '无'}`,
      '[近期画面与当前画面]\n由当前 Realtime 视频流持续提供；按时间理解动作和变化，以最新帧为准。',
    ].join('\n\n');
    this.sendContextUpdate();
  }

  async start(): Promise<void> {
    this.callbacks.onState('connecting');
    this.playbackContext = new AudioContext({ sampleRate: OUTPUT_RATE });
    await this.playbackContext.audioWorklet.addModule(`/duplex-playback.js?v=${REALTIME_CLIENT_BUILD}`);
    this.playbackNode = new AudioWorkletNode(this.playbackContext, 'jiuwen-duplex-playback');
    this.playbackNode.port.onmessage = ({ data }) => {
      if (data.type === 'cleared') {
        if (data.responseId) {
          // The official PyTorch protocol owns listen/speak decisions and does
          // not accept playback acknowledgements or response cancellation.
        }
        this.assistantPlaying = false;
        return;
      }
      if (data.type !== 'drained') return;
      this.assistantPlaying = Boolean(data.hasPending);
      if (!this.assistantPlaying) this.sendContextUpdate();
      if (!this.responseActive && !this.assistantPlaying) this.callbacks.onState('listening');
    };
    this.playbackNode.connect(this.playbackContext.destination);
    await this.playbackContext.resume();

    this.emitDiagnostic('realtime_microphone_request_started', {});
    this.microphone = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
    this.emitDiagnostic('realtime_microphone_ready', {});
    this.captureContext = new AudioContext({ sampleRate: INPUT_RATE });
    await this.captureContext.audioWorklet.addModule('/duplex-capture.js');
    const source = this.captureContext.createMediaStreamSource(this.microphone);
    this.captureNode = new AudioWorkletNode(this.captureContext, 'jiuwen-duplex-capture');
    this.captureNode.port.onmessage = ({ data }) => {
      const pcm = resample(new Int16Array(data), this.captureContext?.sampleRate || INPUT_RATE, INPUT_RATE);
      this.pending.push(pcm);
      this.pendingSamples += pcm.length;
      const maxPending = INPUT_RATE * 2;
      while (this.pendingSamples > maxPending && this.pending.length > 1) {
        this.pendingSamples -= this.pending.shift()?.length || 0;
      }
      if (!this.injecting) {
        const level = this.rms(pcm);
        const frameMs = pcm.length * 1_000 / INPUT_RATE;
        const assistantOutputActive = this.assistantPlaying || this.responseActive || this.bargeInCandidate;
        const threshold = assistantOutputActive
          ? Math.max(1_400, this.noiseFloor * 5)
          : Math.max(700, this.noiseFloor * 3);
        if (level > threshold) {
          this.lastVoiceAt = Date.now();
          this.lastInteractiveInputAt = this.lastVoiceAt;
          this.userSpeechMs += frameMs;
          this.userSilenceMs = 0;
          if (assistantOutputActive && !this.userActivityActive) {
            this.startBargeInCandidate(level, threshold);
          }
          const requiredSpeechMs = this.bargeInCandidate ? BARGE_IN_SPEECH_MS : LISTENING_SPEECH_MS;
          if (!this.userActivityActive && this.userSpeechMs >= requiredSpeechMs) {
            this.userActivityActive = true;
            this.turnHasUserActivity = true;
            // ASR only receives this utterance, not up to 20 seconds of old
            // silence or assistant playback that encourages hallucinations.
            this.turnAudio = this.turnAudio.slice(-1);
            this.turnSamples = this.turnAudio.reduce((total, chunk) => total + chunk.length, 0);
            if (this.bargeInCandidate) {
              this.emitDiagnostic('barge_in_confirmed', {
                level: Math.round(level),
                peak_level: Math.round(this.bargeInPeakLevel),
                threshold: Math.round(this.bargeInThreshold),
                speech_ms: Math.round(this.userSpeechMs),
              });
              this.clearBargeInCandidate(false);
            }
            this.interruptForUserInput();
            this.callbacks.onUserActivity();
          }
        } else {
          this.userSilenceMs += frameMs;
          if (this.bargeInCandidate && !this.userActivityActive) {
            if (this.userSilenceMs >= BARGE_IN_SILENCE_TOLERANCE_MS) {
              this.emitDiagnostic('barge_in_rejected', {
                level: Math.round(level),
                peak_level: Math.round(this.bargeInPeakLevel),
                threshold: Math.round(this.bargeInThreshold),
                speech_ms: Math.round(this.userSpeechMs),
                reason: 'short_noise_or_echo',
              });
              this.clearBargeInCandidate(true);
              this.userSpeechMs = 0;
            }
          } else if (!this.userActivityActive) {
            this.userSpeechMs = 0;
          }
          if (this.userSilenceMs >= USER_TURN_SILENCE_MS) this.userActivityActive = false;
        }
        this.observeNoise(pcm);
      }
    };
    const silent = this.captureContext.createGain();
    silent.gain.value = 0;
    source.connect(this.captureNode);
    this.captureNode.connect(silent).connect(this.captureContext.destination);
    await this.captureContext.resume();

    this.emitDiagnostic('realtime_websocket_connecting', { url: this.config.url });
    await this.openSocket();
    this.sendTimer = window.setInterval(() => this.flush(), SEND_INTERVAL_MS);
  }

  stop(): void {
    if (this.sendTimer !== null) window.clearInterval(this.sendTimer);
    this.sendTimer = null;
    this.send({ type: 'session.close' });
    this.socket?.close(1000, 'client stop');
    this.socket = null;
    this.microphone?.getTracks().forEach((track) => track.stop());
    this.microphone = null;
    void this.captureContext?.close();
    void this.playbackContext?.close();
    this.captureContext = null;
    this.playbackContext = null;
    this.pending = [];
    this.pendingSamples = 0;
    this.pendingTextInputs = [];
    this.pendingToolResults = [];
    this.acceptedToolResultIds.clear();
    this.activeToolJobId = null;
    this.lastInteractiveInputAt = 0;
    this.hasActiveTask = false;
    this.activeTask = '';
    this.lastTaskReminderAt = 0;
    this.sessionReady = false;
    this.lastSentContext = '';
    this.responseActive = false;
    this.pendingUserTurnId = null;
    this.activeAssistantTurnId = null;
    this.pendingResponseLane = null;
    this.activeResponseLane = 'normal';
    this.responseLanes.clear();
    this.userSpeechMs = 0;
    this.userSilenceMs = 0;
    this.userActivityActive = false;
    this.clearBargeInCandidate(false);
    this.cancelledResponseIds.clear();
    this.callbacks.onState('closed');
  }

  async sendAudioDataUrl(dataUrl: string, isFresh: () => boolean = () => true): Promise<void> {
    const queued = this.injectionQueue.then(() => this.injectAudioDataUrl(dataUrl, true, isFresh));
    this.injectionQueue = queued.then(() => undefined, () => undefined);
    await queued;
  }

  async sendTextTurn(text: string, isFresh: () => boolean = () => true): Promise<string | null> {
    const normalized = text.trim();
    if (!normalized || !isFresh()) return null;
    const turnId = this.newTurnId('text');
    this.lastInteractiveInputAt = Date.now();
    this.pendingResponseLane = 'normal';
    this.interruptForUserInput();
    const queued = this.injectionQueue.then(async () => {
      if (!isFresh()) return null;
      this.pendingTextInputs.push({
        text: `<|im_start|>user\n${normalized}<|im_end|>\n`,
        turnId,
        isFresh,
      });
      return turnId;
    });
    this.injectionQueue = queued.then(() => undefined, () => undefined);
    return await queued;
  }

  enqueueToolResult(toolResult: RealtimeToolResult): boolean {
    const jobId = toolResult.jobId.trim();
    const question = toolResult.question.trim();
    const result = toolResult.result.trim();
    if (!jobId || !question || !result || this.acceptedToolResultIds.has(jobId)) return false;
    this.acceptedToolResultIds.add(jobId);
    this.pendingToolResults.push({ jobId, question, result });
    this.emitDiagnostic('search_result_queued', {
      job_id: jobId,
      question,
      result,
    });
    if (this.acceptedToolResultIds.size > 32) {
      const oldest = this.acceptedToolResultIds.values().next().value;
      if (oldest) this.acceptedToolResultIds.delete(oldest);
    }
    return true;
  }

  interruptForUserInput(): void {
    this.pendingResponseLane = 'normal';
    if (!this.assistantPlaying && !this.responseActive && !this.activeToolJobId) return;
    if (this.activeToolJobId) {
      this.emitDiagnostic('search_result_response_interrupted', {
        job_id: this.activeToolJobId,
      });
      this.activeToolJobId = null;
    }
    // MiniCPM accepts force_listen on an input chunk. It closes the speaking
    // turn and resets remote TTS while local playback is cleared immediately.
    this.discardOfficialResponseUntilListen = this.responseActive;
    this.playbackEpoch += 1;
    this.cancelActiveResponse(false);
    this.assistantTranscript = '';
    this.activeAssistantTurnId = null;
    this.sendForceListen();
    this.callbacks.onState('listening');
  }

  private startBargeInCandidate(level: number, threshold: number): void {
    this.bargeInPeakLevel = Math.max(this.bargeInPeakLevel, level);
    if (this.bargeInCandidate) return;
    this.bargeInCandidate = true;
    this.bargeInThreshold = threshold;
    this.playbackNode?.port.postMessage({ type: 'pause' });
    this.emitDiagnostic('barge_in_candidate', {
      level: Math.round(level),
      threshold: Math.round(threshold),
      noise_floor: Math.round(this.noiseFloor),
      assistant_playing: this.assistantPlaying,
      response_active: this.responseActive,
    });
  }

  private clearBargeInCandidate(resumePlayback: boolean): void {
    if (resumePlayback && this.bargeInCandidate) {
      this.playbackNode?.port.postMessage({ type: 'resume' });
    }
    this.bargeInCandidate = false;
    this.bargeInPeakLevel = 0;
    this.bargeInThreshold = 0;
  }

  private emitDiagnostic(event: string, details: Record<string, unknown>): void {
    this.callbacks.onDiagnostic?.({
      event,
      client_time: new Date().toISOString(),
      client_build: REALTIME_CLIENT_BUILD,
      ...details,
    });
  }

  private sendForceListen(): void {
    const silence = new Int16Array(INPUT_RATE);
    this.send({
      type: 'input_audio_buffer.append',
      audio: bytesToBase64(new Uint8Array(silence.buffer)),
      format: 'pcm16',
      sample_rate_hz: INPUT_RATE,
      force_listen: true,
      max_slice_nums: 1,
    });
  }

  private async injectAudioDataUrl(
    dataUrl: string,
    interruptActive: boolean,
    isFresh: () => boolean,
  ): Promise<boolean> {
    const context = this.captureContext || new AudioContext();
    const pcm = await this.decodeDataUrl(dataUrl, context, INPUT_RATE);
    if (!isFresh()) return false;
    if (this.assistantPlaying || this.responseActive) {
      if (!interruptActive) return false;
      this.cancelActiveResponse();
      await new Promise((resolve) => window.setTimeout(resolve, 80));
      if (!isFresh()) return false;
    }
    if (!interruptActive && (this.assistantPlaying || this.responseActive)) return false;
    this.injecting = true;
    this.turnAudio = [];
    this.turnSamples = 0;
    try {
      const chunkSize = INPUT_RATE;
      for (let offset = 0; offset < pcm.length; offset += chunkSize) {
        if (!isFresh()) return false;
        const chunk = pcm.subarray(offset, offset + chunkSize);
        this.sendAudio(chunk, true);
        await new Promise((resolve) => window.setTimeout(resolve, SEND_INTERVAL_MS));
      }
    } finally {
      this.injecting = false;
      this.flush();
    }
    return true;
  }

  private async decodeDataUrl(dataUrl: string, context: AudioContext, targetRate: number): Promise<Int16Array> {
    const encoded = dataUrl.split(',', 2)[1];
    if (!encoded) throw new Error('文字转语音没有返回有效音频');
    const bytes = base64ToBytes(encoded);
    const decoded = await context.decodeAudioData(Uint8Array.from(bytes).buffer);
    const channel = decoded.getChannelData(0);
    const source = new Int16Array(channel.length);
    for (let index = 0; index < channel.length; index += 1) {
      const sample = Math.max(-1, Math.min(1, channel[index]));
      source[index] = sample < 0 ? sample * 32768 : sample * 32767;
    }
    return resample(source, decoded.sampleRate, targetRate);
  }

  private openSocket(): Promise<void> {
    return new Promise((resolve, reject) => {
      const url = new URL(this.config.url);
      url.searchParams.set('mode', 'video');
      url.searchParams.set('duplex', '1');
      url.searchParams.set('model', this.config.model);
      url.searchParams.set('minicpmo45_native_duplex', '1');
      url.searchParams.set('autostart', '0');
      const socket = new WebSocket(url);
      this.socket = socket;
      let initSent = false;
      let settled = false;
      const initTimeout = window.setTimeout(() => {
        if (settled) return;
        settled = true;
        socket.close(1000, 'session init timeout');
        reject(new Error('Realtime 会话初始化超时，远端未返回 session.created'));
      }, 30_000);
      const resolveOnce = () => {
        if (settled) return;
        settled = true;
        window.clearTimeout(initTimeout);
        resolve();
      };
      const rejectOnce = (error: Error) => {
        if (settled) return;
        settled = true;
        window.clearTimeout(initTimeout);
        reject(error);
      };
      const sendInit = async () => {
        if (initSent) return;
        initSent = true;
        this.send({
          type: 'session.update',
          session: {
            model: this.config.model,
            modalities: ['audio', 'text'],
            voice: 'default',
            ref_audio: this.config.refAudio,
            instructions: this.contextInstructions,
            input_audio_format: 'pcm16',
            extra_body: {
              auto_response: true,
              minicpmo45_native_duplex: true,
            },
          },
        });
      };
      socket.onopen = () => {
        this.emitDiagnostic('realtime_websocket_open', { url: url.toString() });
        void sendInit();
      };
      socket.onmessage = ({ data }) => {
        if (this.socket !== socket) return;
        if (typeof data !== 'string') return;
        try {
          const event = JSON.parse(data) as Record<string, unknown>;
          const type = String(event.type || '');
          if (type === 'session.closed' && !this.sessionReady) {
            const closeReason = readableError(event.reason || event.error || '远端在初始化阶段主动关闭');
            this.emitDiagnostic('realtime_websocket_error', {
              url: url.toString(),
              message: closeReason,
            });
            rejectOnce(new Error(`Realtime 会话初始化失败：${closeReason}`));
            return;
          }
          if (type === 'session.queue_done' || type === 'queue_done') {
            void sendInit();
            return;
          }
          if (type === 'session.updated') resolveOnce();
          if (type === 'session.updated') {
            this.sessionReady = true;
            this.lastSentContext = this.contextInstructions;
            this.lastTaskReminderAt = Date.now();
            this.emitDiagnostic('realtime_session_ready', {});
          }
          this.handleEvent(event);
        }
        catch { this.callbacks.onError('Realtime 返回了无效事件'); }
      };
      socket.onerror = () => {
        if (this.socket !== socket) return;
        this.emitDiagnostic('realtime_websocket_error', { url: url.toString() });
        rejectOnce(new Error(`Realtime WebSocket 连接失败：${url}`));
      };
      socket.onclose = ({ code, reason }) => {
        if (this.socket !== socket) return;
        this.emitDiagnostic('realtime_websocket_closed', { code, message: reason });
        if (!this.sessionReady) {
          const closeReason = reason || (code === 1000 ? '远端在初始化阶段主动关闭' : `关闭代码 ${code}`);
          rejectOnce(new Error(`Realtime 会话初始化失败：${closeReason}`));
        }
        this.callbacks.onState('closed');
      };
    });
  }

  private send(event: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(event));
  }

  private sendContextUpdate(): boolean {
    if (!this.sessionReady || this.responseActive
      || this.contextInstructions === this.lastSentContext) return false;
    this.lastSentContext = this.contextInstructions;
    this.send({
      type: 'session.update',
      session: {
        instructions: this.contextInstructions,
      },
    });
    this.lastTaskReminderAt = Date.now();
    this.emitDiagnostic('realtime_context_updated', { has_active_task: this.hasActiveTask });
    return true;
  }

  private flush(): void {
    if (this.injecting || this.pendingSamples < INPUT_RATE) return;
    const outgoing = new Int16Array(INPUT_RATE);
    let written = 0;
    while (written < outgoing.length && this.pending.length > 0) {
      const chunk = this.pending[0];
      const count = Math.min(chunk.length, outgoing.length - written);
      outgoing.set(chunk.subarray(0, count), written);
      written += count;
      this.pendingSamples -= count;
      if (count === chunk.length) this.pending.shift();
      else this.pending[0] = chunk.slice(count);
    }
    this.turnAudio.push(outgoing.slice());
    this.turnSamples += outgoing.length;
    while (this.turnSamples > INPUT_RATE * 20 && this.turnAudio.length > 1) {
      this.turnSamples -= this.turnAudio.shift()?.length || 0;
    }
    this.sendAudio(outgoing, true);
    if (this.turnHasUserActivity && !this.userActivityActive && this.userSilenceMs >= USER_TURN_SILENCE_MS) {
      this.dispatchUserTurn(`turn-${Date.now()}`);
    }
  }

  private rms(pcm: Int16Array): number {
    let energy = 0;
    for (let index = 0; index < pcm.length; index += 8) energy += pcm[index] * pcm[index];
    return Math.sqrt(energy / Math.ceil(pcm.length / 8));
  }

  private discardPendingMic(): void {
    this.pending = [];
    this.pendingSamples = 0;
  }

  private observeNoise(pcm: Int16Array): void {
    const level = this.rms(pcm);
    if (level > this.noiseFloor * 3) return;
    const weight = level < this.noiseFloor ? 0.05 : 0.005;
    this.noiseFloor = Math.max(50, this.noiseFloor * (1 - weight) + level * weight);
  }

  private sendAudio(pcm: Int16Array, includeVideo: boolean): void {
    const contextUpdated = this.sendContextUpdate();
    let dispatchedToolJobId = '';
    const input: Record<string, unknown> = {
      type: 'input_audio_buffer.append',
      audio: bytesToBase64(new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength)),
      format: 'pcm16',
      sample_rate_hz: INPUT_RATE,
      max_slice_nums: 1,
    };
    while (this.pendingTextInputs.length && !this.pendingTextInputs[0].isFresh()) {
      this.pendingTextInputs.shift();
    }
    const pendingText = this.pendingTextInputs.shift();
    if (pendingText) {
      input.text = pendingText.text;
      input.force_speak = true;
      this.pendingUserTurnId = pendingText.turnId;
      this.pendingResponseLane = 'normal';
    } else if (!contextUpdated && this.activeTask && !this.responseActive
      && !this.userActivityActive
      && !this.turnHasUserActivity
      && Date.now() - this.lastTaskReminderAt >= ACTIVE_TASK_REMINDER_INTERVAL_MS) {
      input.text = [
        '<|im_start|>user',
        `[紧急当前任务检查]\n${this.activeTask}`,
        '立即检查本次输入的最新画面；条件满足或有新进展时，输出一句独立、简洁的任务提醒，否则保持倾听。不要回答其他对话，不要复述任务。',
        '<|im_end|>\n',
      ].join('\n');
      this.pendingResponseLane = 'urgent';
      this.lastTaskReminderAt = Date.now();
      this.emitDiagnostic('active_task_reminder_sent', {
        task: this.activeTask,
        source: 'urgent_current_task',
        assistant_playing: this.assistantPlaying,
        response_active: this.responseActive,
      });
    } else if (this.pendingToolResults.length > 0
      && !this.responseActive
      && !this.assistantPlaying
      && !this.userActivityActive
      && !this.turnHasUserActivity
      && Date.now() - this.lastInteractiveInputAt >= 2_500) {
      const toolResult = this.pendingToolResults.shift();
      if (toolResult) {
        input.text = [
          '<|im_start|>user',
          '[异步工具结果]',
          `原始问题：${toolResult.question}`,
          `搜索结果：\n${toolResult.result}`,
          '请严格根据九问主对话模型生成的检索摘要回答上述原始问题，不得补充摘要之外的事实。',
          '摘要没有覆盖问题、证据不足、来源冲突或写明无法确认时，如实说明当前无法确认，不得用常识、记忆或猜测补全。',
          '本轮只回答搜索问题，不执行、不提及也不夹带[当前任务]中的提醒；当前任务由独立的紧急检查轮次处理。',
          '引用只需自然说明来源，不要朗读完整URL。该问题可能来自较早的对话，不要把它与之后的问题混淆。',
          '<|im_end|>\n',
        ].join('\n');
        input.force_speak = true;
        this.pendingResponseLane = 'normal';
        dispatchedToolJobId = toolResult.jobId;
        this.activeToolJobId = toolResult.jobId;
      }
    }
    const shouldSendVideo = this.hasActiveTask || this.injecting || Date.now() - this.lastVoiceAt < 2_500;
    if (includeVideo && shouldSendVideo) {
      const frame = this.callbacks.getVideoFrame();
      if (frame) input.video_frames = [frame];
    }
    this.send(input);
    if (dispatchedToolJobId) {
      this.emitDiagnostic('search_result_dispatched', {
        job_id: dispatchedToolJobId,
      });
      this.callbacks.onToolResultDispatched?.(dispatchedToolJobId);
    }
  }

  private activateResponseLane(responseId: string | null): PlaybackLane {
    const lane = this.pendingResponseLane
      || (this.activeToolJobId || this.pendingUserTurnId ? 'normal' : this.hasActiveTask ? 'urgent' : 'normal');
    this.pendingResponseLane = null;
    this.activeResponseLane = lane;
    if (responseId) {
      this.responseLanes.set(responseId, lane);
      if (this.responseLanes.size > 12) {
        const oldest = this.responseLanes.keys().next().value;
        if (oldest) this.responseLanes.delete(oldest);
      }
    }
    return lane;
  }

  private responseLane(responseId: string | null): PlaybackLane {
    return (responseId && this.responseLanes.get(responseId)) || this.activeResponseLane;
  }

  private handleEvent(event: Record<string, unknown>): void {
    const type = String(event.type || '');
    const response = event.response as Record<string, unknown> | undefined;
    const eventResponseId = String(event.response_id || response?.id || '') || null;
    if (type === 'session.created' || type === 'response.listen') {
      if (type === 'response.listen') {
        this.finishOfficialTurn();
        this.discardOfficialResponseUntilListen = false;
      }
      this.callbacks.onState('listening');
    } else if (type === 'response.output.delta') {
      const kind = String(event.kind || '');
      if (kind === 'listen') {
        this.finishOfficialTurn();
        this.discardOfficialResponseUntilListen = false;
        this.callbacks.onState('listening');
      } else if (kind === 'text') {
        if (this.discardOfficialResponseUntilListen) return;
        this.beginOfficialTurn(eventResponseId);
        const delta = String(event.text || '');
        this.assistantTranscript += delta;
        this.callbacks.onAssistantText(
          this.assistantTranscript,
          false,
          this.activeToolJobId || undefined,
          this.activeAssistantTurnId || undefined,
        );
      } else if (kind === 'audio') {
        if (this.discardOfficialResponseUntilListen) return;
        this.beginOfficialTurn(eventResponseId);
        const encoded = String(event.audio || '');
        if (!encoded || !this.playbackNode) return;
        const playbackEpoch = this.playbackEpoch;
        const lane = this.responseLane(eventResponseId);
        void this.decodeOutputAudio({ format: 'pcm_f32le', sample_rate_hz: OUTPUT_RATE }, encoded).then((output) => {
          if (!output || !this.playbackNode || playbackEpoch !== this.playbackEpoch
            || this.discardOfficialResponseUntilListen) return;
          this.assistantPlaying = true;
          this.playbackNode.port.postMessage({ type: 'audio', lane, pcm: output.buffer }, [output.buffer]);
          // The final short audio chunk can finish decoding after the model has
          // already emitted `listen`. Re-send drain after enqueue so that tail
          // audio below the startup buffer threshold is still played.
          if (!this.responseActive) this.playbackNode.port.postMessage({ type: 'drain', lane });
        }).catch(() => this.callbacks.onError('Realtime 音频解码失败'));
      }
    } else if (type === 'response.created' || type === 'response.speak') {
      if (eventResponseId) this.responseId = eventResponseId;
      if (!this.responseActive) this.activateResponseLane(eventResponseId);
      else if (eventResponseId && !this.responseLanes.has(eventResponseId)) {
        this.responseLanes.set(eventResponseId, this.activeResponseLane);
      }
      this.responseActive = true;
      if (type === 'response.created') {
        this.userSpeechMs = 0;
        this.userSilenceMs = 0;
        this.userActivityActive = false;
        this.assistantTranscript = '';
        this.dispatchUserTurn(eventResponseId || `turn-${Date.now()}`);
      } else {
        this.dispatchUserTurn(eventResponseId || `turn-${Date.now()}`);
      }
      this.bindAssistantTurn();
      this.callbacks.onState('speaking');
    } else if (type === 'audio.cancelled' || type === 'response.audio.cancelled') {
      if (!eventResponseId || eventResponseId === this.responseId) {
        this.responseActive = false;
        this.playbackNode?.port.postMessage({ type: 'clear', cancelResponse: false });
        this.callbacks.onState('listening');
      }
    } else if (type === 'response.audio.delta' || type === 'response.output_audio.delta') {
      const encoded = String(event.delta || event.audio || '');
      if (!encoded || !this.playbackNode) return;
      const responseId = eventResponseId || this.responseId;
      if (responseId && this.cancelledResponseIds.has(responseId)) return;
      const playbackEpoch = this.playbackEpoch;
      const lane = this.responseLane(responseId);
      void this.decodeOutputAudio(event, encoded).then((output) => {
        if (!output || !this.playbackNode || playbackEpoch !== this.playbackEpoch
          || (responseId && this.cancelledResponseIds.has(responseId))) return;
        this.assistantPlaying = true;
        this.playbackNode.port.postMessage({ type: 'audio', lane, pcm: output.buffer, responseId }, [output.buffer]);
      }).catch(() => this.callbacks.onError('Realtime 音频解码失败'));
    } else if (type === 'response.audio.done' || type === 'response.output_audio.done' || type === 'response.done') {
      const affectsActive = !eventResponseId || eventResponseId === this.responseId;
      if (type === 'response.done' && affectsActive) {
        this.responseActive = false;
      }
      const wasCancelled = Boolean(eventResponseId && this.cancelledResponseIds.has(eventResponseId));
      if (affectsActive && !wasCancelled) {
        const responseId = eventResponseId || this.responseId;
        this.playbackNode?.port.postMessage({
          type: 'drain',
          lane: this.responseLane(responseId),
          responseId,
        });
      }
    } else if (type === 'response.audio_transcript.delta') {
      if (eventResponseId && this.cancelledResponseIds.has(eventResponseId)) return;
      const delta = String(event.delta || '');
      if (!delta) return;
      if (delta.startsWith(this.assistantTranscript)) this.assistantTranscript = delta;
      else if (!this.assistantTranscript.endsWith(delta)) this.assistantTranscript += delta;
      this.callbacks.onAssistantText(
        this.assistantTranscript,
        false,
        this.activeToolJobId || undefined,
        this.activeAssistantTurnId || undefined,
      );
    } else if (type === 'response.audio_transcript.done') {
      if (eventResponseId && this.cancelledResponseIds.has(eventResponseId)) return;
      this.assistantTranscript = String(event.transcript || this.assistantTranscript);
      this.finishAssistantText();
    } else if (type === 'conversation.item.input_audio_transcription.delta') {
      this.callbacks.onUserText(String(event.delta || ''), false);
    } else if (type === 'conversation.item.input_audio_transcription.completed') {
      this.callbacks.onUserText(String(event.transcript || ''), true);
    } else if (type === 'error') {
      this.callbacks.onError(readableError(event.error || event));
    }
  }

  private beginOfficialTurn(responseId: string | null): void {
    if (this.responseActive) return;
    this.activateResponseLane(responseId);
    this.responseActive = true;
    this.assistantTranscript = '';
    this.dispatchUserTurn(`turn-${Date.now()}`);
    this.bindAssistantTurn();
    this.callbacks.onState('speaking');
  }

  private finishOfficialTurn(): void {
    const wasSpeaking = this.responseActive || this.assistantPlaying;
    const lane = this.activeResponseLane;
    this.responseActive = false;
    if (this.assistantTranscript) {
      this.finishAssistantText();
    } else if (this.activeToolJobId) {
      this.emitDiagnostic('search_result_response_empty', {
        job_id: this.activeToolJobId,
      });
      this.activeToolJobId = null;
    }
    this.activeAssistantTurnId = null;
    if (wasSpeaking) this.playbackNode?.port.postMessage({ type: 'drain', lane });
    this.pendingResponseLane = null;
    this.activeResponseLane = 'normal';
  }

  private finishAssistantText(): void {
    const text = this.assistantTranscript;
    const toolJobId = this.activeToolJobId || undefined;
    const turnId = this.activeAssistantTurnId || undefined;
    this.callbacks.onAssistantText(text, true, toolJobId, turnId);
    this.emitDiagnostic('realtime_answer_final', {
      realtime_answer: text,
      ...(toolJobId ? { job_id: toolJobId } : {}),
      ...(turnId ? { turn_id: turnId } : {}),
    });
    if (toolJobId) {
      this.emitDiagnostic('search_result_answered', {
        job_id: toolJobId,
        realtime_answer: text,
      });
    }
    this.assistantTranscript = '';
    this.activeToolJobId = null;
    this.activeAssistantTurnId = null;
  }

  private rememberCancelledResponse(): void {
    if (!this.responseId) return;
    this.cancelledResponseIds.add(this.responseId);
    if (this.cancelledResponseIds.size <= 8) return;
    const oldest = this.cancelledResponseIds.values().next().value;
    if (oldest) this.cancelledResponseIds.delete(oldest);
  }

  private cancelActiveResponse(discardPendingMic = true): void {
    const responseId = this.responseId;
    const cancelRemote = this.responseActive;
    if (responseId && (this.assistantPlaying || cancelRemote) && !this.cancelledResponseIds.has(responseId)) {
      this.rememberCancelledResponse();
    }
    if (discardPendingMic) this.discardPendingMic();
    this.playbackNode?.port.postMessage({ type: 'clear', cancelResponse: cancelRemote });
    this.assistantPlaying = false;
    this.responseActive = false;
    this.responseId = null;
    this.activeAssistantTurnId = null;
  }

  private async decodeOutputAudio(event: Record<string, unknown>, encoded: string): Promise<Int16Array | null> {
    const bytes = base64ToBytes(encoded);
    const format = String(event.format || event.audio_format || 'pcm16').toLowerCase();
    let pcm: Int16Array;
    let sourceRate = Number(event.sample_rate_hz || event.sample_rate || OUTPUT_RATE);
    if (format.includes('wav')) {
      if (!this.playbackContext) return null;
      const decoded = await this.playbackContext.decodeAudioData(Uint8Array.from(bytes).buffer);
      const samples = decoded.getChannelData(0);
      pcm = new Int16Array(samples.length);
      for (let index = 0; index < samples.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, samples[index]));
        pcm[index] = sample < 0 ? sample * 32768 : sample * 32767;
      }
      sourceRate = decoded.sampleRate;
    } else if (format.includes('f32')) {
      const samples = new Float32Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 4));
      pcm = new Int16Array(samples.length);
      for (let index = 0; index < samples.length; index += 1) {
        const sample = Math.max(-1, Math.min(1, samples[index]));
        pcm[index] = sample < 0 ? sample * 32768 : sample * 32767;
      }
    } else {
      pcm = new Int16Array(bytes.buffer, bytes.byteOffset, Math.floor(bytes.byteLength / 2));
    }
    return resample(pcm, sourceRate, this.playbackContext?.sampleRate || OUTPUT_RATE);
  }

  private takeTurnAudio(): string {
    const pcm = new Int16Array(this.turnSamples);
    let offset = 0;
    for (const chunk of this.turnAudio) { pcm.set(chunk, offset); offset += chunk.length; }
    this.turnAudio = [];
    this.turnSamples = 0;
    const buffer = new ArrayBuffer(44 + pcm.byteLength);
    const view = new DataView(buffer);
    const write = (at: number, value: string) => {
      for (let index = 0; index < value.length; index += 1) view.setUint8(at + index, value.charCodeAt(index));
    };
    write(0, 'RIFF'); view.setUint32(4, 36 + pcm.byteLength, true); write(8, 'WAVE');
    write(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true);
    view.setUint16(22, 1, true); view.setUint32(24, INPUT_RATE, true);
    view.setUint32(28, INPUT_RATE * 2, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true);
    write(36, 'data'); view.setUint32(40, pcm.byteLength, true);
    new Uint8Array(buffer, 44).set(new Uint8Array(pcm.buffer));
    return `data:audio/wav;base64,${bytesToBase64(new Uint8Array(buffer))}`;
  }

  private dispatchUserTurn(turnId: string): void {
    if (!this.turnHasUserActivity || this.turnSamples < INPUT_RATE / 2) return;
    this.turnHasUserActivity = false;
    this.pendingUserTurnId = turnId;
    this.callbacks.onTurnAudio(this.takeTurnAudio(), turnId);
  }

  private bindAssistantTurn(): void {
    if (this.activeToolJobId || this.activeAssistantTurnId || !this.pendingUserTurnId) return;
    this.activeAssistantTurnId = this.pendingUserTurnId;
    this.pendingUserTurnId = null;
  }

  private newTurnId(prefix: string): string {
    this.turnSequence += 1;
    return prefix + '-' + Date.now() + '-' + this.turnSequence;
  }
}
