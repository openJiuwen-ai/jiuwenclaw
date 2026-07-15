import type { ModelEntry } from "../../types";

export interface ModelIdentity {
  originIndex?: number;
  fallbackIndex: number;
}

export function findModelIndex(models: ModelEntry[], identity: ModelIdentity): number {
  if (identity.originIndex !== undefined) {
    const persistedIndex = models.findIndex((model) => model.origin_index === identity.originIndex);
    return persistedIndex;
  }
  return identity.fallbackIndex >= 0 && identity.fallbackIndex < models.length
    ? identity.fallbackIndex
    : -1;
}

function modelNonAuthFieldsEqual(left: ModelEntry, right: ModelEntry): boolean {
  return (left.alias ?? "") === (right.alias ?? "")
    && (left.reasoning_level ?? "") === (right.reasoning_level ?? "")
    && left.is_default === right.is_default
    && (left.temperature ?? 0.95) === (right.temperature ?? 0.95)
    && (left.timeout ?? 1800) === (right.timeout ?? 1800);
}

export function modelEntriesEqual(left: ModelEntry, right: ModelEntry): boolean {
  return left.model_name === right.model_name
    && left.api_base === right.api_base
    && left.api_key === right.api_key
    && left.model_provider === right.model_provider
    && modelNonAuthFieldsEqual(left, right);
}

export function patchModelSnapshot(
  models: ModelEntry[],
  identity: ModelIdentity,
  patch: Partial<ModelEntry>,
): ModelEntry[] {
  const targetIndex = findModelIndex(models, identity);
  if (targetIndex < 0) return models;

  const nextModels = [...models];
  nextModels[targetIndex] = { ...nextModels[targetIndex], ...patch };
  return nextModels;
}

export function canAutoSaveOpenAIAccountModel(
  draftModels: ModelEntry[],
  persistedModels: ModelEntry[],
  identity: ModelIdentity,
): boolean {
  if (identity.originIndex === undefined || draftModels.length !== persistedModels.length) {
    return false;
  }

  const targetIndex = findModelIndex(draftModels, identity);
  const persistedTargetIndex = findModelIndex(persistedModels, identity);
  if (targetIndex < 0 || targetIndex !== persistedTargetIndex) return false;

  return draftModels.every((draftModel, index) => {
    const persistedModel = persistedModels[index];
    if (index === targetIndex) {
      return Boolean(persistedModel && modelNonAuthFieldsEqual(draftModel, persistedModel));
    }
    return Boolean(
      persistedModel
      && draftModel.origin_index === persistedModel.origin_index
      && modelEntriesEqual(draftModel, persistedModel),
    );
  });
}
