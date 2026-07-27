/** 配置引导内各步共享的数据结构。 */

export interface OnboardingModelForm {
  model_name: string;
  api_base: string;
  api_key: string;
  model_provider: string;
  reasoning_level: string;
}

export type ModelValidateState = 'idle' | 'validating' | 'ok' | 'err';

/** 经典模式第三方 API Key（与 ConfigPanel 的 THIRD_PARTY_API_KEYS 对齐）。 */
export interface OnboardingSearchKeys {
  jina_api_key: string;
  bocha_api_key: string;
  perplexity_api_key: string;
  serper_api_key: string;
  github_token: string;
}

export const EMPTY_MODEL_FORM: OnboardingModelForm = {
  model_name: '',
  api_base: '',
  api_key: '',
  model_provider: '',
  reasoning_level: '',
};

export const EMPTY_SEARCH_KEYS: OnboardingSearchKeys = {
  jina_api_key: '',
  bocha_api_key: '',
  perplexity_api_key: '',
  serper_api_key: '',
  github_token: '',
};

/** jiuwenswarm 特性开关（与 ConfigPanel「其他配置」页签的布尔项对齐）。 */
export type OnboardingFeatureKey =
  | 'free_search_bing_enabled'
  | 'free_search_ddg_enabled'
  | 'evolution_auto_scan'
  | 'skill_create'
  | 'context_engine_enabled'
  | 'a2ui_enabled'
  | 'swarmflow_enabled'
  | 'symphony_enabled'
  | 'symphony_dynamic_graph_enabled'
  | 'skill_retrieval_enabled'
  | 'proactive_recommendation_enabled';

export type OnboardingFeatures = Record<OnboardingFeatureKey, boolean>;

/** 一律以关闭态展示（不回填真实配置），仅将用户手动改动过的开关写回。 */
export const EMPTY_FEATURES: OnboardingFeatures = {
  free_search_bing_enabled: false,
  free_search_ddg_enabled: false,
  evolution_auto_scan: false,
  skill_create: false,
  context_engine_enabled: false,
  a2ui_enabled: false,
  swarmflow_enabled: false,
  symphony_enabled: false,
  symphony_dynamic_graph_enabled: false,
  skill_retrieval_enabled: false,
  proactive_recommendation_enabled: false,
};

/** 免费搜索开关，归入「第三方服务配置」分类展示。 */
export const ONBOARDING_SEARCH_FEATURES: OnboardingFeatureKey[] = [
  'free_search_bing_enabled',
  'free_search_ddg_enabled',
];

/**
 * 高级功能开关，按配置中心「其他配置」页签的原有分组与顺序组织。
 * 仅保留开关类配置项（数值项如推荐频率等不在引导中展示）。
 */
export const ONBOARDING_FEATURE_GROUPS: { id: string; keys: OnboardingFeatureKey[] }[] = [
  { id: 'evolution', keys: ['evolution_auto_scan', 'skill_create'] },
  { id: 'context_engine', keys: ['context_engine_enabled'] },
  { id: 'a2ui', keys: ['a2ui_enabled'] },
  { id: 'swarmflow', keys: ['swarmflow_enabled'] },
  {
    id: 'symphony',
    keys: ['symphony_enabled', 'symphony_dynamic_graph_enabled', 'skill_retrieval_enabled'],
  },
  { id: 'proactive', keys: ['proactive_recommendation_enabled'] },
];
