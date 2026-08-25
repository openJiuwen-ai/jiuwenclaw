import type { FunctionComponent, SVGProps } from 'react';
import GeneralIcon from './navigation/general.svg?react';
import ModelsIcon from './navigation/models.svg?react';
import AgentIcon from './navigation/agent.svg?react';
import BrowserIcon from './navigation/browser.svg?react';
import ChannelsIcon from './navigation/channels.svg?react';
import ExperimentalIcon from './navigation/experimental.svg?react';
import RefreshIcon from './actions/refresh.svg?react';
import EditIcon from './actions/edit.svg?react';
import EnableIcon from './actions/enable.svg?react';
import DisableIcon from './actions/disable.svg?react';
import DeleteIcon from '../delete.svg?react';
import emptyBoxIllustration from './empty/empty-box.svg';
import openAi48 from './providers/48/openai.svg';
import deepSeek48 from './providers/48/deepseek.svg';
import dashScope48 from './providers/48/dashscope.svg';
import zhipu48 from './providers/48/zhipu.svg';
import kimi48 from './providers/48/kimi.svg';
import miniMax48 from './providers/48/minimax.svg';
import anthropic48 from './providers/48/anthropic.svg';
import tencentCloud48 from './providers/48/tencent-cloud.svg';
import huaweiCloud48 from './providers/48/huawei-cloud.svg';
import custom48 from './providers/48/custom.svg';
import openAi16 from './providers/16/openai.svg';
import deepSeek16 from './providers/16/deepseek.svg';
import dashScope16 from './providers/16/dashscope.svg';
import zhipu16 from './providers/16/zhipu.svg';
import kimi16 from './providers/16/kimi.svg';
import miniMax16 from './providers/16/minimax.svg';
import anthropic16 from './providers/16/anthropic.svg';
import tencentCloud16 from './providers/16/tencent-cloud.svg';
import huaweiCloud16 from './providers/16/huawei-cloud.svg';
import custom16 from './providers/16/custom.svg';
import xiaoyiLogo from './channels/xiaoyi.svg';
import feishuLogo from './channels/feishu.svg';
import dingtalkLogo from './channels/dingtalk.svg';
import telegramLogo from './channels/telegram.svg';
import discordLogo from './channels/discord.svg';
import slackLogo from './channels/slack.svg';
import whatsappLogo from './channels/whatsapp.svg';

export type SettingsNavigationIcon = FunctionComponent<SVGProps<SVGSVGElement>>;

export const settingsNavigationIcons = {
  general: GeneralIcon,
  models: ModelsIcon,
  agent: AgentIcon,
  browser: BrowserIcon,
  channels: ChannelsIcon,
  experimental: ExperimentalIcon,
} as const satisfies Record<string, SettingsNavigationIcon>;

export const settingsActionIcons = {
  refresh: RefreshIcon,
  edit: EditIcon,
  enable: EnableIcon,
  disable: DisableIcon,
  delete: DeleteIcon,
} as const;

export const settingsEmptyBoxIllustration = emptyBoxIllustration;

export const settingsProviderLogos48 = {
  OpenAI: openAi48,
  OpenAIAccount: openAi48,
  DeepSeek: deepSeek48,
  DashScope: dashScope48,
  Zhipu: zhipu48,
  Kimi: kimi48,
  MiniMax: miniMax48,
  Anthropic: anthropic48,
  TencentCloud: tencentCloud48,
  HuaweiCloud: huaweiCloud48,
  Custom: custom48,
} as const;

export const settingsProviderLogos16 = {
  OpenAI: openAi16,
  OpenAIAccount: openAi16,
  DeepSeek: deepSeek16,
  DashScope: dashScope16,
  Zhipu: zhipu16,
  Kimi: kimi16,
  MiniMax: miniMax16,
  Anthropic: anthropic16,
  TencentCloud: tencentCloud16,
  HuaweiCloud: huaweiCloud16,
  Custom: custom16,
} as const;

export const settingsChannelLogos = {
  xiaoyi: xiaoyiLogo,
  feishu: feishuLogo,
  dingtalk: dingtalkLogo,
  telegram: telegramLogo,
  discord: discordLogo,
  slack: slackLogo,
  whatsapp: whatsappLogo,
} as const;

export function getSettingsProviderLogo48(provider: string): string | undefined {
  return settingsProviderLogos48[provider as keyof typeof settingsProviderLogos48];
}

export function getSettingsProviderLogo16(provider: string): string | undefined {
  return settingsProviderLogos16[provider as keyof typeof settingsProviderLogos16];
}
