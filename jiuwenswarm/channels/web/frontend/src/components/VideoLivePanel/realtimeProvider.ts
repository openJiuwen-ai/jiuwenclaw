import {
  RealtimeDuplexCallbacks,
  RealtimeDuplexSession,
} from '../../utils/realtimeDuplex';
import { VideoSessionConfig } from './types';

export { RealtimeDuplexSession } from '../../utils/realtimeDuplex';

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
  if (!config.ref_audio_base64) throw new Error('请配置 Full-duplex 参考音频');
  return new RealtimeDuplexSession({
    url: config.url,
    model: config.model,
    refAudio: `data:audio/wav;base64,${config.ref_audio_base64}`,
  }, callbacks);
}
