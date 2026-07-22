import type { ModelEntry } from "../../types";

export const CODEX_SUBSCRIPTION_PROVIDER = "AI4ResearchCodex";
export const CODEX_SUBSCRIPTION_MODEL = "codex-subscription";
export const OPENAI_ACCOUNT_PROVIDER = "OpenAIAccount";

export type ApiProviderSnapshot = Pick<
  ModelEntry,
  "model_provider" | "model_name" | "api_base" | "api_key"
>;

export interface ModelProviderTransition {
  model: ModelEntry;
  snapshot: ApiProviderSnapshot | null;
}

export function isOpenAIAccountProvider(provider?: string): boolean {
  return (provider || "").trim().toLowerCase() === OPENAI_ACCOUNT_PROVIDER.toLowerCase();
}

export function isCodexSubscriptionProvider(provider?: string): boolean {
  return (provider || "").trim() === CODEX_SUBSCRIPTION_PROVIDER;
}

export function modelRequiresApiKey(provider?: string): boolean {
  return !isOpenAIAccountProvider(provider) && !isCodexSubscriptionProvider(provider);
}

export function modelRequiresApiBase(provider?: string): boolean {
  return !isCodexSubscriptionProvider(provider);
}

export function buildCodexSubscriptionModelDefaults(): ApiProviderSnapshot {
  return {
    model_provider: CODEX_SUBSCRIPTION_PROVIDER,
    model_name: CODEX_SUBSCRIPTION_MODEL,
    api_base: "",
    api_key: "",
  };
}

export function captureApiProviderSnapshot(
  model: ModelEntry,
): ApiProviderSnapshot | null {
  if (!modelRequiresApiKey(model.model_provider)) return null;
  return {
    model_provider: model.model_provider,
    model_name: model.model_name,
    api_base: model.api_base,
    api_key: model.api_key,
  };
}

export function transitionModelProvider(
  current: ModelEntry,
  targetProvider: string,
  previousSnapshot: ApiProviderSnapshot | null = null,
): ModelProviderTransition {
  if (isCodexSubscriptionProvider(targetProvider)) {
    return {
      model: { ...current, ...buildCodexSubscriptionModelDefaults() },
      snapshot: captureApiProviderSnapshot(current) ?? previousSnapshot,
    };
  }

  if (isCodexSubscriptionProvider(current.model_provider)) {
    if (previousSnapshot?.model_provider === targetProvider) {
      return {
        model: { ...current, ...previousSnapshot },
        snapshot: previousSnapshot,
      };
    }
    return {
      model: {
        ...current,
        model_provider: targetProvider,
        model_name: "",
        api_base: "",
        api_key: "",
      },
      snapshot: previousSnapshot,
    };
  }

  return {
    model: { ...current, model_provider: targetProvider },
    snapshot: previousSnapshot,
  };
}
