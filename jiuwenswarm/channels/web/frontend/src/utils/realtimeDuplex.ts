export interface RealtimeDuplexConfig {
  url: string;
  model: string;
  refAudio: string;
}

export interface RealtimeDuplexCallbacks {
  getVideoFrame: () => string | null;
  onAssistantText: (text: string, final: boolean) => void;
  onUserText: (text: string, final: boolean) => void;
  onUserActivity: () => void;
  onTurnAudio: (wavDataUrl: string, turnId: string) => void;
  onState: (state: 'connecting' | 'listening' | 'speaking' | 'closed') => void;
  onError: (message: string) => void;
}

const INPUT_RATE = 16_000;
const OUTPUT_RATE = 24_000;
const SEND_INTERVAL_MS = 1_000;
const USER_TURN_SILENCE_MS = 900;
const BARGE_IN_SPEECH_MS = 240;
const BARGE_IN_RMS_FLOOR = 1_800;
const BASE_INSTRUCTIONS = [
  '你是九问实时视觉助手。',
  '始终结合当前会话中的近期聊天、近期画面和最新画面回答；最新画面优先，不得把已经消失的物体当成仍在画面中。',
  '当前任务是需要持续执行的视觉任务。任务不为“无”时，持续观察画面、维护进度，并仅在任务规定的时机主动说话；没有新进展时保持倾听。',
  '不要因为每帧画面而重复回答。持续出现的同一事件只介入一次；消失后再次出现视为新事件，可以再次介入。一个动作只在完整周期结束后计数。',
  '用户可以随时询问进度、修改、暂停或取消当前任务。回答使用自然、简洁的中文。',
  '用户提出新任务时只确认开始观察，不得把任务描述中的目标当成已经发生；只有目标在[当前任务]中且最新画面确认满足时才提醒。',
  '简单询问画面中的物体或品牌是什么时直接识别回答；只有用户明确要求介绍公司、查询背景或最新外部信息时，才简短说“我帮你查一下”，系统会接续九问搜索结果。',
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
  private contextInstructions = `${BASE_INSTRUCTIONS}\n\n[当前任务]\n无`;
  private lastSentContext = '';
  private sessionReady = false;
  private responseId: string | null = null;
  private cancelledResponseIds = new Set<string>();
  private injecting = false;
  private injectionQueue: Promise<void> = Promise.resolve();
  private pendingTextInputs: Array<{ text: string; isFresh: () => boolean }> = [];
  private turnAudio: Int16Array[] = [];
  private turnSamples = 0;
  private assistantPlaying = false;
  private responseActive = false;
  private noiseFloor = 120;
  private userSpeechMs = 0;
  private userSilenceMs = 0;
  private userActivityActive = false;
  private turnHasUserActivity = false;
  private assistantTranscript = '';
  private bargeInSpeechMs = 0;
  private forceListenNextChunk = false;
  private interruptPending = false;

  constructor(
    private readonly config: RealtimeDuplexConfig,
    private readonly callbacks: RealtimeDuplexCallbacks,
  ) {}

  updateContext(currentTask: string, recentChat: ReadonlyArray<{ role: string; text: string }>): void {
    this.hasActiveTask = Boolean(currentTask.trim());
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
      `[当前任务]\n${currentTask.trim() || '无'}`,
      `[当前聊天]\n${chat || '无'}`,
      '[近期画面与当前画面]\n由当前 Realtime 视频流持续提供；按时间理解动作和变化，以最新帧为准。',
    ].join('\n\n');
    this.sendContextUpdate();
  }

  async start(): Promise<void> {
    this.callbacks.onState('connecting');
    this.playbackContext = new AudioContext({ sampleRate: OUTPUT_RATE });
    await this.playbackContext.audioWorklet.addModule('/duplex-playback.js');
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
      this.assistantPlaying = false;
      this.sendContextUpdate();
      if (!this.responseActive) this.callbacks.onState('listening');
    };
    this.playbackNode.connect(this.playbackContext.destination);
    await this.playbackContext.resume();

    this.microphone = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
    });
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
        if (this.assistantPlaying && !this.interruptPending) {
          const threshold = Math.max(BARGE_IN_RMS_FLOOR, this.noiseFloor * 8);
          this.bargeInSpeechMs = level > threshold ? this.bargeInSpeechMs + frameMs : 0;
          if (this.bargeInSpeechMs >= BARGE_IN_SPEECH_MS) this.interrupt();
        } else {
          this.bargeInSpeechMs = 0;
        }
        if (level > Math.max(700, this.noiseFloor * 3)) {
          this.lastVoiceAt = Date.now();
          this.userSpeechMs += frameMs;
          this.userSilenceMs = 0;
          if (!this.userActivityActive && this.userSpeechMs >= 120) {
            this.userActivityActive = true;
            this.turnHasUserActivity = true;
            // ASR only receives this utterance, not up to 20 seconds of old
            // silence or assistant playback that encourages hallucinations.
            this.turnAudio = this.turnAudio.slice(-1);
            this.turnSamples = this.turnAudio.reduce((total, chunk) => total + chunk.length, 0);
            this.callbacks.onUserActivity();
          }
        } else {
          this.userSpeechMs = 0;
          this.userSilenceMs += frameMs;
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
    this.forceListenNextChunk = false;
    this.interruptPending = false;
    this.bargeInSpeechMs = 0;
    this.pendingTextInputs = [];
    this.hasActiveTask = false;
    this.sessionReady = false;
    this.lastSentContext = '';
    this.responseActive = false;
    this.userSpeechMs = 0;
    this.userSilenceMs = 0;
    this.userActivityActive = false;
    this.cancelledResponseIds.clear();
    this.callbacks.onState('closed');
  }

  interrupt(): boolean {
    if (!this.sessionReady || this.interruptPending
      || (!this.responseActive && !this.assistantPlaying)) return false;
    this.interruptPending = true;
    this.forceListenNextChunk = true;
    this.bargeInSpeechMs = 0;
    if (this.assistantTranscript) {
      this.callbacks.onAssistantText(this.assistantTranscript, true);
      this.assistantTranscript = '';
    }
    this.playbackNode?.port.postMessage({ type: 'clear', cancelResponse: false });
    this.assistantPlaying = false;
    this.callbacks.onState('listening');
    return true;
  }

  async sendAudioDataUrl(dataUrl: string, isFresh: () => boolean = () => true): Promise<void> {
    const queued = this.injectionQueue.then(() => this.injectAudioDataUrl(dataUrl, true, isFresh));
    this.injectionQueue = queued.then(() => undefined, () => undefined);
    await queued;
  }

  async sendTextTurn(text: string, isFresh: () => boolean = () => true): Promise<boolean> {
    const normalized = text.trim();
    if (!normalized || !isFresh()) return false;
    const queued = this.injectionQueue.then(async () => {
      const deadline = Date.now() + 45_000;
      while ((this.responseActive || this.assistantPlaying) && Date.now() < deadline) {
        await new Promise((resolve) => window.setTimeout(resolve, 50));
        if (!isFresh()) return false;
      }
      if (!isFresh() || this.responseActive || this.assistantPlaying) return false;
      this.pendingTextInputs.push({
        text: `<|im_start|>user\n${normalized}<|im_end|>\n`,
        isFresh,
      });
      return true;
    });
    this.injectionQueue = queued.then(() => undefined, () => undefined);
    return await queued;
  }

  holdForTool(): void {
    if (!this.sessionReady) return;
    this.responseActive = false;
    this.assistantPlaying = false;
    this.assistantTranscript = '';
    this.playbackNode?.port.postMessage({ type: 'clear', cancelResponse: false });
    this.send({
      type: 'input.append',
      input: {
        text: '<|im_start|>system\n九问工具正在执行。等待工具结果期间保持倾听，不要自行回答。<|im_end|>\n',
        force_listen: true,
      },
    });
    this.callbacks.onState('listening');
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
      const socket = new WebSocket(url);
      this.socket = socket;
      let initSent = false;
      const sendInit = async () => {
        if (initSent) return;
        initSent = true;
        try {
          const pcm = await this.decodeDataUrl(this.config.refAudio, this.captureContext || new AudioContext(), INPUT_RATE);
          const samples = new Float32Array(pcm.length);
          for (let index = 0; index < pcm.length; index += 1) samples[index] = pcm[index] / 32768;
          this.send({
            type: 'session.init',
            payload: {
              system_prompt: this.contextInstructions,
              ref_audio_base64: bytesToBase64(new Uint8Array(samples.buffer)),
              config: { length_penalty: 1.1, listen_prob_scale: 0.5 },
            },
          });
        } catch (error) {
          reject(error);
        }
      };
      socket.onopen = () => undefined;
      socket.onmessage = ({ data }) => {
        if (typeof data !== 'string') return;
        try {
          const event = JSON.parse(data) as Record<string, unknown>;
          const type = String(event.type || '');
          if (type === 'session.queue_done' || type === 'queue_done') {
            void sendInit();
            return;
          }
          if (type === 'session.created') resolve();
          if (type === 'session.created') {
            this.sessionReady = true;
            this.lastSentContext = this.contextInstructions;
          }
          this.handleEvent(event);
        }
        catch { this.callbacks.onError('Realtime 返回了无效事件'); }
      };
      socket.onerror = () => reject(new Error(`Realtime WebSocket 连接失败：${url}`));
      socket.onclose = () => this.callbacks.onState('closed');
    });
  }

  private send(event: Record<string, unknown>): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(event));
  }

  private sendContextUpdate(): void {
    if (!this.sessionReady || this.responseActive || this.assistantPlaying
      || this.contextInstructions === this.lastSentContext) return;
    this.lastSentContext = this.contextInstructions;
    this.send({
      type: 'input.append',
      input: {
        text: `<|im_start|>system\n${this.contextInstructions}<|im_end|>\n`,
        force_listen: true,
      },
    });
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
    const fixed = new Float32Array(INPUT_RATE);
    const sampleCount = Math.min(pcm.length, INPUT_RATE);
    for (let index = 0; index < sampleCount; index += 1) fixed[index] = pcm[index] / 32768;
    const input: Record<string, unknown> = {
      audio: bytesToBase64(new Uint8Array(fixed.buffer)),
      max_slice_nums: 1,
    };
    const forcingListen = this.forceListenNextChunk;
    if (forcingListen) {
      input.force_listen = true;
      this.forceListenNextChunk = false;
    }
    while (this.pendingTextInputs.length && !this.pendingTextInputs[0].isFresh()) {
      this.pendingTextInputs.shift();
    }
    const pendingText = forcingListen ? undefined : this.pendingTextInputs.shift();
    if (pendingText) {
      input.text = pendingText.text;
      input.force_speak = true;
    }
    const shouldSendVideo = this.hasActiveTask || this.injecting || Date.now() - this.lastVoiceAt < 2_500;
    if (includeVideo && shouldSendVideo) {
      const frame = this.callbacks.getVideoFrame();
      if (frame) input.video_frames = [frame];
    }
    this.send({ type: 'input.append', input });
  }

  private handleEvent(event: Record<string, unknown>): void {
    const type = String(event.type || '');
    const response = event.response as Record<string, unknown> | undefined;
    const eventResponseId = String(event.response_id || response?.id || '') || null;
    if (type === 'session.created' || type === 'response.listen') {
      if (type === 'response.listen') this.finishOfficialTurn();
      this.callbacks.onState('listening');
    } else if (type === 'response.output.delta') {
      const kind = String(event.kind || '');
      if (kind === 'listen') {
        this.interruptPending = false;
        this.finishOfficialTurn();
        this.callbacks.onState('listening');
      } else if (kind === 'text') {
        if (this.interruptPending) return;
        this.beginOfficialTurn();
        const delta = String(event.text || '');
        this.assistantTranscript += delta;
        this.callbacks.onAssistantText(this.assistantTranscript, false);
      } else if (kind === 'audio') {
        if (this.interruptPending) return;
        this.beginOfficialTurn();
        const encoded = String(event.audio || '');
        if (!encoded || !this.playbackNode) return;
        void this.decodeOutputAudio({ format: 'pcm_f32le', sample_rate_hz: OUTPUT_RATE }, encoded).then((output) => {
          if (!output || !this.playbackNode || this.interruptPending) return;
          this.assistantPlaying = true;
          this.playbackNode.port.postMessage({ type: 'audio', pcm: output.buffer }, [output.buffer]);
          // The final short audio chunk can finish decoding after the model has
          // already emitted `listen`. Re-send drain after enqueue so that tail
          // audio below the startup buffer threshold is still played.
          if (!this.responseActive) this.playbackNode.port.postMessage({ type: 'drain' });
        }).catch(() => this.callbacks.onError('Realtime 音频解码失败'));
      }
    } else if (type === 'response.created' || type === 'response.speak') {
      if (eventResponseId) this.responseId = eventResponseId;
      this.responseActive = true;
      if (type === 'response.created') {
        this.userSpeechMs = 0;
        this.userSilenceMs = 0;
        this.userActivityActive = false;
        this.assistantTranscript = '';
        this.dispatchUserTurn(eventResponseId || `turn-${Date.now()}`);
      }
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
      void this.decodeOutputAudio(event, encoded).then((output) => {
        if (!output || !this.playbackNode || (responseId && this.cancelledResponseIds.has(responseId))) return;
        this.assistantPlaying = true;
        this.playbackNode.port.postMessage({ type: 'audio', pcm: output.buffer, responseId }, [output.buffer]);
      }).catch(() => this.callbacks.onError('Realtime 音频解码失败'));
    } else if (type === 'response.audio.done' || type === 'response.output_audio.done' || type === 'response.done') {
      const affectsActive = !eventResponseId || eventResponseId === this.responseId;
      if (type === 'response.done' && affectsActive) {
        this.responseActive = false;
      }
      const wasCancelled = Boolean(eventResponseId && this.cancelledResponseIds.has(eventResponseId));
      if (affectsActive && !wasCancelled) {
        this.playbackNode?.port.postMessage({ type: 'drain', responseId: eventResponseId || this.responseId });
      }
    } else if (type === 'response.audio_transcript.delta') {
      if (eventResponseId && this.cancelledResponseIds.has(eventResponseId)) return;
      const delta = String(event.delta || '');
      if (!delta) return;
      if (delta.startsWith(this.assistantTranscript)) this.assistantTranscript = delta;
      else if (!this.assistantTranscript.endsWith(delta)) this.assistantTranscript += delta;
      this.callbacks.onAssistantText(this.assistantTranscript, false);
    } else if (type === 'response.audio_transcript.done') {
      if (eventResponseId && this.cancelledResponseIds.has(eventResponseId)) return;
      this.assistantTranscript = String(event.transcript || this.assistantTranscript);
      this.callbacks.onAssistantText(this.assistantTranscript, true);
    } else if (type === 'conversation.item.input_audio_transcription.delta') {
      this.callbacks.onUserText(String(event.delta || ''), false);
    } else if (type === 'conversation.item.input_audio_transcription.completed') {
      this.callbacks.onUserText(String(event.transcript || ''), true);
    } else if (type === 'error') {
      this.callbacks.onError(readableError(event.error || event));
    }
  }

  private beginOfficialTurn(): void {
    if (this.responseActive) return;
    this.responseActive = true;
    this.assistantTranscript = '';
    this.dispatchUserTurn(`turn-${Date.now()}`);
    this.callbacks.onState('speaking');
  }

  private finishOfficialTurn(): void {
    const wasSpeaking = this.responseActive || this.assistantPlaying;
    this.responseActive = false;
    if (this.assistantTranscript) {
      this.callbacks.onAssistantText(this.assistantTranscript, true);
      this.assistantTranscript = '';
    }
    if (wasSpeaking) this.playbackNode?.port.postMessage({ type: 'drain' });
  }

  private rememberCancelledResponse(): void {
    if (!this.responseId) return;
    this.cancelledResponseIds.add(this.responseId);
    if (this.cancelledResponseIds.size <= 8) return;
    const oldest = this.cancelledResponseIds.values().next().value;
    if (oldest) this.cancelledResponseIds.delete(oldest);
  }

  private cancelActiveResponse(): void {
    const responseId = this.responseId;
    const cancelRemote = this.responseActive;
    if (responseId && (this.assistantPlaying || cancelRemote) && !this.cancelledResponseIds.has(responseId)) {
      this.rememberCancelledResponse();
    }
    this.discardPendingMic();
    this.playbackNode?.port.postMessage({ type: 'clear', cancelResponse: cancelRemote });
    this.assistantPlaying = false;
    this.responseActive = false;
    this.responseId = null;
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
    this.callbacks.onTurnAudio(this.takeTurnAudio(), turnId);
  }
}
