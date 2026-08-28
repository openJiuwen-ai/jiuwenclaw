export const MINICPM_CURRENT_TASK_MONITORING_ENABLED = false;

const CURRENT_TASK_INSTRUCTIONS = [
  '当前任务是需要持续执行的视觉任务。任务不为“无”时，持续观察画面、维护进度，并仅在任务规定的时机主动说话；没有新进展时保持倾听。',
  '所有当前任务提醒都按紧急事件处理：条件满足时只输出一句独立提醒，不要夹带正在处理的普通对话或搜索回答。',
  '不要因为每帧画面而重复回答。持续出现的同一事件只介入一次；消失后再次出现视为新事件，可以再次介入。一个动作只在完整周期结束后计数。',
  '用户可以随时询问进度、修改、暂停或取消当前任务。回答使用自然、简洁的中文。',
  '用户提出新任务时只确认开始观察，不得把任务描述中的目标当成已经发生；只有目标在[当前任务]中且最新画面确认满足时才提醒。',
];

export const MINICPM_BASE_INSTRUCTIONS = [
  '你是九问实时视觉助手。',
  '始终结合当前会话中的近期聊天、近期画面和最新画面回答；最新画面优先，不得把已经消失的物体当成仍在画面中。',
  '只把当前可见画面、当前可辨语音、用户明确提供的信息和九问工具结果作为事实依据。画面模糊、文字不完整、对象无法确认时，明确说明无法确认或请用户调整画面，不得猜测品牌、文字、人物、数量或状态。',
  '天气、新闻、价格、公司背景、人物资料、地点信息及其他需要外部知识或时效性的事实，当前画面和用户输入不是足够证据。在收到[异步工具结果]前，必须明确回答“我目前不知道，需要搜索确认”，不得给出任何实质结论，不得用模型记忆或常识补全答案。',
  '例如用户询问“香港今天天气如何”时，未收到支持该地点和日期的[异步工具结果]前，不得说晴、阴、雨、温度、湿度或其他具体天气信息。',
  '收到九问检索摘要后，只回答摘要正文能够直接支持的内容。摘要表示材料不足、存在冲突或无法确认时，必须保留该不确定性，不得自行补齐结论。',
  ...(MINICPM_CURRENT_TASK_MONITORING_ENABLED ? CURRENT_TASK_INSTRUCTIONS : []),
  '简单询问当前画面中清晰可见的物体或品牌是什么时可直接识别回答；用户询问公司介绍、背景资料、天气、新闻、价格或其他外部事实时，只说当前不知道并需要搜索确认，系统会接续九问搜索结果。',
  '用户要求持续观察、搜索外部信息或停止当前任务时，使用自然语言明确说明你理解的操作和对象；不要输出JSON、工具标签或内部控制格式。',
].join('\n');

const MINICPM_SESSION_INSTRUCTIONS = `Streaming Omni Conversation.\n${MINICPM_BASE_INSTRUCTIONS}`;

export function createMiniCpmSessionUpdate(refAudio?: string): Record<string, unknown> {
  return {
    type: 'session.update',
    session: {
      modalities: ['audio', 'text'],
      voice: 'default',
      ref_audio: refAudio,
      instructions: MINICPM_SESSION_INSTRUCTIONS,
      extra_body: {
        auto_response: true,
        minicpmo45_native_duplex: true,
      },
    },
  };
}

export function createMiniCpmAudioAppendEvent(
  audio: string,
  sampleRate: number,
  frame: string | null,
): Record<string, unknown> {
  return {
    type: 'input_audio_buffer.append',
    audio,
    format: 'pcm16',
    sample_rate_hz: sampleRate,
    ...(frame ? { video_frames: [frame] } : {}),
  };
}

export function createMiniCpmPlaybackAckEvent(
  responseId: unknown,
  playedMs: unknown,
): Record<string, unknown> | null {
  if (!responseId || !playedMs) return null;
  return {
    type: 'playback.ack',
    response_id: responseId,
    item_id: `item_${responseId}`,
    played_ms: playedMs,
    committed_ms: playedMs,
  };
}

export function createMiniCpmTaskControl(task: string): string {
  return task
    ? [
      '<|im_start|>system',
      `[当前任务已更新]\n${task}`,
      '从现在开始持续执行该任务；不要把任务条件当成已经发生。此控制消息无需单独回答。',
      '<|im_end|>\n',
    ].join('\n')
    : [
      '<|im_start|>system',
      '[当前任务已停止]\n立即停止之前的持续观察任务，仅继续普通实时对话。此控制消息无需单独回答。',
      '<|im_end|>\n',
    ].join('\n');
}

export function createMiniCpmTaskReminder(task: string): string {
  return [
    '<|im_start|>user',
    `[紧急当前任务检查]\n${task}`,
    '立即检查本次输入的最新画面；条件满足或有新进展时，输出一句独立、简洁的任务提醒，否则保持倾听。不要回答其他对话，不要复述任务。',
    '<|im_end|>\n',
  ].join('\n');
}

export function createMiniCpmContextInstructions(
  task: string,
  recentChat: ReadonlyArray<{ role: string; text: string }>,
): string {
  const chat = recentChat
    .slice(-8)
    .map((item) => {
      const speaker = item.role === 'assistant' ? '助手' : item.role === 'tool' ? '九问工具结果' : '用户';
      return `${speaker}：${item.text.trim()}`;
    })
    .filter((line) => !line.endsWith('：'))
    .join('\n');
  return [
    MINICPM_BASE_INSTRUCTIONS,
    ...(MINICPM_CURRENT_TASK_MONITORING_ENABLED ? [`[当前任务]\n${task || '无'}`] : []),
    `[当前聊天]\n${chat || '无'}`,
    '[近期画面与当前画面]\n由当前 Realtime 视频流持续提供；按时间理解动作和变化，以最新帧为准。',
  ].join('\n\n');
}

export function createMiniCpmTextAppendEvent(text: string): Record<string, unknown> {
  return { type: 'input.text.append', text };
}

export function createMiniCpmCloseSessionEvent(): Record<string, unknown> {
  return { type: 'session.close' };
}
