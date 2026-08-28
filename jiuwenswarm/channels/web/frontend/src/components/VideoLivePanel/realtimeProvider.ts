import {
  RealtimeDuplexCallbacks,
  RealtimeDuplexSession,
} from '../../utils/realtimeDuplex';
import { getWsBase } from '../../utils/env';
import { VideoSessionConfig } from './types';

export { RealtimeDuplexSession } from '../../utils/realtimeDuplex';

function resolveRealtimeUrl(configuredUrl: string): string {
  if (/^wss?:\/\//i.test(configuredUrl)) return configuredUrl;
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const configuredBase = getWsBase();
  const browserBase = configuredBase || `${protocol}//${window.location.host}`;
  const base = new URL(browserBase);
  return new URL(configuredUrl, `${base.protocol}//${base.host}`).toString();
}

export function createRealtimeProvider(
  config: VideoSessionConfig,
  callbacks: RealtimeDuplexCallbacks,
  onUnsupportedBrowser?: () => void,
): RealtimeDuplexSession {
  if (!navigator.mediaDevices?.getUserMedia || !window.AudioWorkletNode) {
    onUnsupportedBrowser?.();
    throw new Error('当前浏览器不支持 Full-duplex 音频。');
  }
  if (!config.url) throw new Error('请配置 Full-duplex WebSocket 地址');
  const dialect = config.dialect || (config.provider === 'qwen_omni' ? 'qwen_omni' : 'minicpm');
  if (dialect === 'minicpm' && !config.ref_audio_base64) {
    throw new Error('请配置 Full-duplex 参考音频');
  }
  return new RealtimeDuplexSession({
    url: resolveRealtimeUrl(config.url),
    dialect,
    voice: config.voice,
    tools: config.tools,
    refAudio: config.ref_audio_base64
      ? `data:audio/wav;base64,${config.ref_audio_base64}`
      : undefined,
  }, callbacks);
}
