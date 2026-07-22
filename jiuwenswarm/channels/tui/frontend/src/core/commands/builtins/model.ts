import { addError, addInfo } from "../helpers.js";
import { CommandKind, type SlashCommand } from "../types.js";
import {
  getOpenAIAccountAuthStatus,
  isOpenAIAccountProvider,
  isOpenAIAccountReady,
  type OpenAIAccountAuthStatus,
} from "./auth.js";

export interface ModelMeta {
  name: string;
  alias?: string;
  model_name?: string;
  model_provider?: string;
  api_base?: string;
  api_key_suffix?: string;
  is_current?: boolean;
  reasoning_level?: string;
}

export interface ModelListPayload {
  current?: string;
  available_models?: string[];
  models?: ModelMeta[];
}

export type ModelConfigField =
  | "model_name"
  | "alias"
  | "api_base"
  | "api_key"
  | "model_provider"
  | "reasoning_level";

export const SUPPORTED_MODEL_PROVIDERS = [
  "OpenAI",
  "OpenRouter",
  "DashScope",
  "SiliconFlow",
  "InferenceAffinity",
  "DeepSeek",
  "OpenAIAccount",
] as const;

export const MANUALLY_CREATABLE_MODEL_PROVIDERS = SUPPORTED_MODEL_PROVIDERS.filter(
  (provider) => provider !== "OpenAIAccount",
);

const MODEL_REQUEST_TIMEOUT_MS = 75_000;
const OAUTH_EDITABLE_MODEL_FIELDS = new Set<ModelConfigField>(["alias", "reasoning_level"]);

export function isSupportedModelProvider(provider: string): boolean {
  const normalized = provider.trim().toLowerCase();
  return SUPPORTED_MODEL_PROVIDERS.some((candidate) => candidate.toLowerCase() === normalized);
}

export function isManuallyCreatableModelProvider(provider: string): boolean {
  const normalized = provider.trim().toLowerCase();
  return MANUALLY_CREATABLE_MODEL_PROVIDERS.some(
    (candidate) => candidate.toLowerCase() === normalized,
  );
}

export function isModelConfigFieldEditable(
  field: ModelConfigField,
  provider: string,
  mode: "add" | "edit",
): boolean {
  return (
    mode === "add" || !isOpenAIAccountProvider(provider) || OAUTH_EDITABLE_MODEL_FIELDS.has(field)
  );
}

/** Reserved keys under config.yaml `models` for multimodal profiles; configure via /config, not via /model switch */
const RESERVED_MULTIMODAL_MODEL_KEYS = new Set(["video", "audio", "vision"]);

export function isReservedMultimodalModelKey(name: string): boolean {
  return RESERVED_MULTIMODAL_MODEL_KEYS.has(name.trim().toLowerCase());
}

export function createModelCommand(): SlashCommand {
  return {
    name: "model",
    description: "View, add, edit, delete, or switch AI models defined in config.yaml",
    usage: "/model [name] | /model add <name> <key=value>...",
    example: "/model work (switch)\n/model add work model_name=gpt-4 api_key=xxx model_provider=OpenAI",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    action: async (ctx, args) => {
      const raw = args.trim();

      // 1. Handle Add Model: /model add <name> <key=value> ...
      if (raw.match(/^add\s+\S+/)) {
        const parts = raw.split(/\s+/);
        if (parts.length < 3) {
          ctx.addItem(addInfo(ctx.sessionId, "Usage: /model add <name> key=value ...", "m"));
          return;
        }

        const target = parts[1];
        const settings: Record<string, string> = {};
        for (let i = 2; i < parts.length; i++) {
          const eqIdx = parts[i].indexOf("=");
          if (eqIdx > 0) {
            const rawKey = parts[i].substring(0, eqIdx);
            const key = rawKey === "model_provider" ? "provider" : rawKey;
            const val = parts[i].substring(eqIdx + 1);
            settings[key] = val;
          }
        }

        const requestedProvider = settings.provider ?? settings.client_provider ?? "";
        if (isOpenAIAccountProvider(requestedProvider)) {
          ctx.addItem(
            addError(
              ctx.sessionId,
              "OpenAI Account models are managed through /auth login or /auth models.",
            ),
          );
          return;
        }

        try {
          const payload = await ctx.request<{
            saved?: boolean;
            applied?: boolean;
            apply_error?: string;
          }>(
            "command.model",
            {
              action: "add_model",
              target: target,
              config: settings,
            },
            MODEL_REQUEST_TIMEOUT_MS,
          );
          if (payload.saved === true && payload.applied !== true) {
            ctx.addItem(
              addError(
                ctx.sessionId,
                payload.apply_error ??
                  "Model configuration was saved but not applied; restart or retry.",
              ),
            );
            return;
          }
          ctx.addItem(
            addInfo(ctx.sessionId, `Added/Updated model config: ${target}`, "m", {
              view: "kv",
              items: Object.entries(settings).map(([k, v]) => ({
                label: k,
                value: k.toLowerCase().includes("key") || k.toLowerCase().includes("token") ? "****" : v,
              })),
            }),
          );
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          ctx.addItem(addError(ctx.sessionId, `Failed to add model: ${message}`));
        }
        return;
      }

      // 2. Handle View and Switch
      const value = raw;
      try {
        // If no arg or "list", show selectable model list
        if (value === "" || value === "list") {
          const payload = await ctx.request<ModelListPayload>("command.model", {});
          const models = payload.available_models ?? [];
          const current = payload.current ?? "unknown";
          if (models.length === 0) {
            ctx.addItem(addInfo(ctx.sessionId, "No models configured", "m"));
            return;
          }
          const skipped = models.filter((m) => isReservedMultimodalModelKey(m));
          const selectableWithOriginalIndex = models
            .map((name, index) => ({ name, index }))
            .filter((entry) => !isReservedMultimodalModelKey(entry.name));
          const selectable = selectableWithOriginalIndex.map((entry) => entry.name);
          if (skipped.length > 0) {
            ctx.addItem(
              addInfo(
                ctx.sessionId,
                "video, audio, and vision are not offered as the default chat model here (multimodal-only). To configure them, use /config edit → Vision / Audio / Video, or /config set on keys such as vision_model, audio_model, video_model.",
                "m",
              ),
            );
          }
          if (selectable.length === 0) {
            ctx.addItem(addInfo(ctx.sessionId, "No switchable models in list", "m"));
            return;
          }
          const modelsMeta = payload.models ?? [];
          let openAIAccountStatus: OpenAIAccountAuthStatus | null = null;
          if (modelsMeta.some((meta) => isOpenAIAccountProvider(meta.model_provider))) {
            try {
              openAIAccountStatus = await getOpenAIAccountAuthStatus(ctx.request);
            } catch {
              openAIAccountStatus = null;
            }
          }
          // 优先用后端 is_current 标记判断当前模型（同名模型仅靠名字无法区分），
          // 回退到 name-matching（兼容不带 is_current 的旧后端）
          const currentIdx = selectableWithOriginalIndex.findIndex((entry) => {
            const meta = modelsMeta[entry.index];
            return meta?.is_current === true;
          });
          const fallbackCurrentIdx = currentIdx < 0 ? selectable.findIndex((m) => m === current) : currentIdx;
          const nameOccurrence: Record<string, number> = {};
          const items = selectableWithOriginalIndex.map((entry, i) => {
            const m = entry.name;
            const meta = modelsMeta[entry.index];
            const isCurrent = i === fallbackCurrentIdx;
            // 统计同名出现次序
            const seq = (nameOccurrence[m] ?? 0) + 1;
            nameOccurrence[m] = seq;
            const sameNameTotal = selectable.filter((x) => x === m).length;
            let displayName: string;
            if (sameNameTotal > 1) {
              displayName = meta?.model_name
                ? `${meta.model_name} #${seq}`
                : `${m} #${seq}`;
            } else if (meta?.model_name && meta.model_name !== m) {
              displayName = `${m} (${meta.model_name})`;
            } else {
              displayName = m;
            }
            const isOpenAIAccount = isOpenAIAccountProvider(meta?.model_provider);
            const availability = isOpenAIAccount
              ? openAIAccountStatus === null
                ? "auth status unavailable"
                : isOpenAIAccountReady(openAIAccountStatus)
                  ? ""
                  : "login required"
              : "";
            // 仅当同名模型且 provider+api_base 也完全相同时（真正无法区分）才显示 key 末4位
            // 避免泄露过多 key 明文，且只在必要时露出尾号
            let keySuffix = "";
            if (sameNameTotal > 1 && meta?.api_key_suffix) {
              const _mk = (mm: ModelMeta | undefined) =>
                `${mm?.model_provider ?? ""}|${mm?.api_base ?? ""}`;
              const myFingerprint = _mk(meta);
              const conflictCount = selectableWithOriginalIndex.reduce((acc, candidate) => {
                const xm = modelsMeta[candidate.index];
                return xm && _mk(xm) === myFingerprint ? acc + 1 : acc;
              }, 0);
              if (conflictCount > 1) {
                keySuffix = ` […${meta.api_key_suffix}]`;
              }
            }
            return {
              label: String(i + 1),
              value: `${displayName}${keySuffix}${isCurrent ? " (current)" : ""}${availability ? ` (${availability})` : ""}`,
            };
          });
          ctx.addItem(
            addInfo(ctx.sessionId, `Available models (${selectable.length} total)`, "m", {
              view: "list",
              title: "Switch Model",
              items,
            }),
          );
          return;
        }

        if (isReservedMultimodalModelKey(value)) {
          ctx.addItem(
            addError(
              ctx.sessionId,
              "Cannot use /model to select video, audio, or vision as the default chat model. Configure multimodal APIs in /config edit (Vision / Audio / Video) or /config set (e.g. vision_model, audio_model, video_model).",
            ),
          );
          return;
        }

        // Switch to specific model
        const configured = await ctx.request<ModelListPayload>("command.model", {});
        const targetMeta = (configured.models ?? []).find(
          (meta) => meta.name === value || meta.alias === value || meta.model_name === value,
        );
        if (targetMeta && isOpenAIAccountProvider(targetMeta.model_provider)) {
          const status = await getOpenAIAccountAuthStatus(ctx.request);
          if (!isOpenAIAccountReady(status)) {
            ctx.addItem(
              addError(
                ctx.sessionId,
                `Model '${value}' is unavailable because OpenAI Account is logged out. Run /auth login first.`,
              ),
            );
            return;
          }
        }
        const payload = await ctx.request<{
          current?: string;
          requested?: string;
          saved?: boolean;
          applied?: boolean;
          apply_error?: string;
          type?: string;
        }>("command.model", { model: value }, MODEL_REQUEST_TIMEOUT_MS);

        const isSwitch = !!payload.requested;
        if (isSwitch) {
          if (payload.applied !== true) {
            ctx.addItem(
              addError(
                ctx.sessionId,
                payload.apply_error ??
                  "Model configuration was saved but not applied; restart or retry.",
              ),
            );
            return;
          }
          ctx.setModel(payload.current ?? payload.requested ?? "");
        }
        const title = isSwitch
          ? `Switched to: ${payload.current ?? payload.requested}`
          : "Model Configuration";
        const icon = isSwitch ? "m" : "c";

        ctx.addItem(
          addInfo(
            ctx.sessionId,
            payload.requested
              ? `Switched model config to: ${payload.current ?? payload.requested}`
              : `Current model: ${payload.current ?? "unknown"}`,
            icon,
            {
              view: "kv",
              title,
              items: [
                { label: "current", value: payload.current ?? "unknown" },
                ...(payload.type ? [{ label: "type", value: payload.type }] : []),
                ...(typeof payload.applied === "boolean"
                  ? [{ label: "applied", value: String(payload.applied) }]
                  : []),
              ],
            },
          ),
        );
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.addItem(addError(ctx.sessionId, `model failed: ${message}`));
      }
    },
  };
}
