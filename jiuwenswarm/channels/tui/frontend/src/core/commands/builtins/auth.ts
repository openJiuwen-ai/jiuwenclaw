import { WsRequestError } from "../../ws-client.js";
import { QuestionBusyError, QuestionCancelledError } from "../../question-errors.js";
import { addError, addInfo } from "../helpers.js";
import { CommandKind, type CommandContext, type SlashCommand } from "../types.js";
import type { ModelListPayload, ModelMeta } from "./model.js";

export const OPENAI_ACCOUNT_PROVIDER = "OpenAIAccount";
const MIN_LOGIN_POLL_INTERVAL_MS = 15_000;
const AUTH_REQUEST_TIMEOUT_MS = 45_000;
const MODEL_REQUEST_TIMEOUT_MS = 75_000;
const LOGIN_START_TIMEOUT_MS = 90_000;

export interface OpenAIAccountAuthStatus {
  authenticated: boolean;
  auth_path?: string;
  has_refresh_token?: boolean;
  expires_at?: number | null;
  needs_refresh?: boolean;
  error?: string | null;
  base_url?: string;
}

interface OpenAIAccountLoginPayload {
  status: "pending" | "none";
  login_id?: string;
  user_code?: string;
  verification_uri?: string;
  interval?: number;
  expires_in?: number;
  expires_at?: number;
  auth?: OpenAIAccountAuthStatus;
}

interface OpenAIAccountPollPayload {
  status: "pending" | "authenticated" | "expired";
  authenticated?: boolean;
  expires_at?: number;
  auth?: OpenAIAccountAuthStatus;
}

interface OpenAIAccountModelsPayload {
  models?: string[];
  base_url?: string;
  auth?: OpenAIAccountAuthStatus;
}

interface LoginOptions {
  minPollIntervalMs?: number;
  nowMs?: () => number;
  sleep?: (delayMs: number) => Promise<void>;
}

interface CurrentModelState {
  name: string;
  modelId: string;
  provider: string;
  meta?: ModelMeta;
}

const KEEP_CURRENT_MODEL = "Keep current model";

export function isOpenAIAccountProvider(provider: string | undefined): boolean {
  return (provider ?? "").trim().toLowerCase() === OPENAI_ACCOUNT_PROVIDER.toLowerCase();
}

export function isOpenAIAccountReady(status: OpenAIAccountAuthStatus | null | undefined): boolean {
  return status?.authenticated === true;
}

export async function getOpenAIAccountAuthStatus(
  request: CommandContext["request"],
): Promise<OpenAIAccountAuthStatus> {
  return request<OpenAIAccountAuthStatus>(
    "openai_account.auth.status",
    {},
    AUTH_REQUEST_TIMEOUT_MS,
  );
}

export function parseOpenAIAccountModelArgument(args: string): string | undefined {
  const value = args.trim();
  if (!value) return undefined;
  const match = /^model=(.+)$/i.exec(value);
  const modelId = (match?.[1] ?? value).trim();
  if (!modelId || /\s/.test(modelId)) {
    throw new Error("model must be a single model ID");
  }
  return modelId;
}

export function getOpenAIAccountPollDelayMs(
  intervalSeconds: number | undefined,
  minimumMs = MIN_LOGIN_POLL_INTERVAL_MS,
): number {
  const serverDelay =
    typeof intervalSeconds === "number" && Number.isFinite(intervalSeconds)
      ? Math.max(0, intervalSeconds) * 1000
      : 0;
  return Math.max(minimumMs, serverDelay);
}

export function getOpenAIAccountLoginTtlMs(
  expiresInSeconds: number | undefined,
  fallbackSeconds = 300,
): number {
  const seconds =
    typeof expiresInSeconds === "number" &&
    Number.isFinite(expiresInSeconds) &&
    expiresInSeconds >= 0
      ? expiresInSeconds
      : fallbackSeconds;
  return Math.max(0, seconds) * 1000;
}

export function isRetryableOpenAIAccountError(error: unknown): boolean {
  return error instanceof WsRequestError && error.retriable;
}

function requiresOpenAIAccountRelogin(error: unknown): boolean {
  return error instanceof WsRequestError && error.payload.relogin_required === true;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

async function waitForPollDelay(
  ctx: CommandContext,
  delayMs: number,
  loginDeadlineMs: number,
  nowMs: () => number,
  sleep: (delayMs: number) => Promise<void>,
): Promise<boolean> {
  const pollDeadlineMs = Math.min(loginDeadlineMs, nowMs() + Math.max(0, delayMs));
  while (nowMs() < pollDeadlineMs) {
    if (ctx.isInterruptRequested()) return false;
    const remaining = pollDeadlineMs - nowMs();
    await sleep(Math.min(100, remaining));
  }
  return !ctx.isInterruptRequested();
}

function resolveCurrentModel(payload: ModelListPayload): CurrentModelState {
  const models = payload.models ?? [];
  const meta = models.find((model) => model.is_current === true) ?? models[0];
  return {
    name: meta?.name || payload.current || meta?.model_name || "not selected",
    modelId: meta?.model_name || meta?.name || payload.current || "",
    provider: meta?.model_provider || "unknown",
    meta,
  };
}

function showAuthStatus(
  ctx: CommandContext,
  status: OpenAIAccountAuthStatus,
  current?: CurrentModelState,
): void {
  const readiness = current?.meta
    ? isOpenAIAccountProvider(current.provider)
      ? isOpenAIAccountReady(status)
        ? "ready"
        : "login required"
      : `using ${current.provider}`
    : "no configured model";
  ctx.addItem(
    addInfo(
      ctx.sessionId,
      status.authenticated ? "OpenAI Account is connected" : "OpenAI Account is not connected",
      "c",
      {
        view: "kv",
        title: "OpenAI Account",
        items: [
          { label: "authenticated", value: String(status.authenticated) },
          { label: "needs refresh", value: String(status.needs_refresh === true) },
          ...(status.expires_at
            ? [{ label: "expires", value: new Date(status.expires_at * 1000).toISOString() }]
            : []),
          ...(status.auth_path ? [{ label: "auth store", value: status.auth_path }] : []),
          ...(status.error ? [{ label: "status", value: status.error }] : []),
          ...(current
            ? [
                { label: "current model", value: current.name },
                { label: "provider", value: current.provider },
                { label: "model readiness", value: readiness },
              ]
            : []),
        ],
      },
    ),
  );
}

function normalizedModelIds(payload: OpenAIAccountModelsPayload): string[] {
  return (payload.models ?? [])
    .filter((modelId): modelId is string => typeof modelId === "string")
    .map((modelId) => modelId.trim())
    .filter(Boolean);
}

async function listOpenAIAccountModels(ctx: CommandContext): Promise<OpenAIAccountModelsPayload> {
  const payload = await ctx.request<OpenAIAccountModelsPayload>(
    "openai_account.models.list",
    {},
    MODEL_REQUEST_TIMEOUT_MS,
  );
  const models = normalizedModelIds(payload);
  return { ...payload, models };
}

async function getConfiguredModels(ctx: CommandContext): Promise<ModelListPayload> {
  return ctx.request<ModelListPayload>("command.model", {});
}

function resolveCatalogModelId(requested: string, catalog: OpenAIAccountModelsPayload): string {
  const modelIds = normalizedModelIds(catalog);
  if (/^\d+$/.test(requested)) {
    const index = Number(requested) - 1;
    if (index < 0 || index >= modelIds.length) {
      throw new Error(`model index must be between 1 and ${modelIds.length}`);
    }
    return modelIds[index];
  }
  return requested;
}

async function promptForOpenAIAccountModel(
  ctx: CommandContext,
  catalog: OpenAIAccountModelsPayload,
  configured: ModelListPayload,
): Promise<void> {
  const modelIds = normalizedModelIds(catalog);
  if (modelIds.length === 0) {
    ctx.addItem(addInfo(ctx.sessionId, "No OpenAI Account models available", "m"));
    return;
  }

  const current = resolveCurrentModel(configured);
  const configuredIds = new Set(
    (configured.models ?? [])
      .filter((meta) => isOpenAIAccountProvider(meta.model_provider))
      .map((meta) => meta.model_name || meta.name)
      .filter(Boolean),
  );
  const currentIsOpenAIAccount =
    current.meta !== undefined && isOpenAIAccountProvider(current.provider);
  const options = modelIds.map((modelId) => ({
    label: modelId,
    description:
      currentIsOpenAIAccount && current.modelId === modelId
        ? "Current OpenAI Account model"
        : configuredIds.has(modelId)
          ? "Configured; select to make current"
          : "Available; select to add and use",
  }));
  if (current.meta && !currentIsOpenAIAccount) {
    options.unshift({
      label: KEEP_CURRENT_MODEL,
      description: `Keep ${current.name} (${current.provider})`,
    });
  }

  let selected: string | undefined;
  try {
    const [answer] = await ctx.askQuestions(
      [
        {
          header: "OpenAI model",
          question: "Select the model to use for new conversations:",
          options,
        },
      ],
      "openai_account_model",
    );
    selected = answer?.selected_options?.[0];
  } catch (error) {
    if (error instanceof QuestionCancelledError) {
      selected = undefined;
    } else {
      throw error;
    }
  }

  if (!selected) {
    ctx.addItem(
      addInfo(
        ctx.sessionId,
        `Model selection cancelled; current model remains ${current.name}`,
        "m",
      ),
    );
    return;
  }
  if (selected === KEEP_CURRENT_MODEL) {
    ctx.addItem(addInfo(ctx.sessionId, `Current model remains ${current.name}`, "m"));
    return;
  }
  await useOpenAIAccountModel(ctx, selected, catalog);
}

async function useOpenAIAccountModel(
  ctx: CommandContext,
  modelId: string,
  catalog?: OpenAIAccountModelsPayload,
): Promise<void> {
  const available = catalog ?? (await listOpenAIAccountModels(ctx));
  const modelIds = normalizedModelIds(available);
  if (!modelIds.includes(modelId)) {
    throw new Error(`OpenAI Account model '${modelId}' is not available`);
  }

  const switched = await ctx.request<{
    current?: string;
    requested?: string;
    saved?: boolean;
    applied?: boolean;
    apply_error?: string;
  }>("openai_account.models.use", { model_id: modelId }, MODEL_REQUEST_TIMEOUT_MS);
  if (switched.saved !== true || switched.applied !== true) {
    throw new Error(
      switched.apply_error ?? "Model configuration was saved but not applied; restart or retry.",
    );
  }
  const current = switched.current ?? modelId;
  ctx.setModel(current);
  ctx.addItem(addInfo(ctx.sessionId, `OpenAI Account model saved and selected: ${current}`, "m"));
}

async function finishAuthenticatedLogin(
  ctx: CommandContext,
  status: OpenAIAccountAuthStatus,
  catalog: OpenAIAccountModelsPayload,
  requestedModel: string | undefined,
): Promise<void> {
  let configured: ModelListPayload;
  try {
    configured = await getConfiguredModels(ctx);
  } catch (error) {
    showAuthStatus(ctx, status);
    ctx.addItem(
      addError(
        ctx.sessionId,
        `OpenAI Account is connected, but the current model could not be loaded: ${errorMessage(error)}`,
      ),
    );
    return;
  }
  const current = resolveCurrentModel(configured);
  showAuthStatus(ctx, status, current);

  try {
    if (requestedModel) {
      await useOpenAIAccountModel(ctx, resolveCatalogModelId(requestedModel, catalog), catalog);
      return;
    }

    if (
      current.meta &&
      isOpenAIAccountProvider(current.provider) &&
      normalizedModelIds(catalog).includes(current.modelId)
    ) {
      ctx.addItem(
        addInfo(
          ctx.sessionId,
          `Current OpenAI Account model remains selected: ${current.name}`,
          "m",
        ),
      );
      return;
    }

    await promptForOpenAIAccountModel(ctx, catalog, configured);
  } catch (error) {
    const selectionError =
      error instanceof QuestionBusyError
        ? `${error.message}; finish the active question and try /auth models again`
        : errorMessage(error);
    ctx.addItem(
      addError(
        ctx.sessionId,
        `OpenAI Account is connected, but model selection failed: ${selectionError}`,
      ),
    );
  }
}

async function runLogin(
  ctx: CommandContext,
  args: string,
  options: LoginOptions = {},
): Promise<void> {
  let requestedModel: string | undefined;
  try {
    requestedModel = parseOpenAIAccountModelArgument(args);
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    ctx.addItem(addError(ctx.sessionId, `login failed: ${message}`));
    return;
  }

  ctx.clearInterruptRequested();
  ctx.setRunningCommand?.("openai-account-login");
  try {
    const status = await getOpenAIAccountAuthStatus(ctx.request);
    if (status.authenticated) {
      try {
        const models = await listOpenAIAccountModels(ctx);
        await finishAuthenticatedLogin(ctx, models.auth ?? status, models, requestedModel);
        return;
      } catch (error) {
        if (!requiresOpenAIAccountRelogin(error)) {
          showAuthStatus(ctx, status);
          ctx.addItem(
            addError(
              ctx.sessionId,
              `OpenAI Account is connected, but the model catalog could not be loaded: ${errorMessage(error)}`,
            ),
          );
          return;
        }
      }
    }

    let login = await ctx.request<OpenAIAccountLoginPayload>(
      "openai_account.auth.pending_login",
      {},
      AUTH_REQUEST_TIMEOUT_MS,
    );
    if (login.status !== "pending") {
      login = await ctx.request<OpenAIAccountLoginPayload>(
        "openai_account.auth.start_login",
        {},
        LOGIN_START_TIMEOUT_MS,
      );
    }
    if (!login.login_id || !login.user_code || !login.verification_uri) {
      throw new Error("OpenAI Account login response is incomplete");
    }
    const loginTtlMs = getOpenAIAccountLoginTtlMs(login.expires_in);

    ctx.addItem(
      addInfo(ctx.sessionId, "Complete OpenAI Account authorization in your browser", "c", {
        view: "kv",
        title: "OpenAI Account Login",
        items: [
          { label: "URL", value: login.verification_uri },
          { label: "code", value: login.user_code },
          { label: "expires in", value: `${Math.ceil(loginTtlMs / 1000)} seconds` },
        ],
      }),
    );

    const pollDelayMs = getOpenAIAccountPollDelayMs(
      login.interval,
      options.minPollIntervalMs ?? MIN_LOGIN_POLL_INTERVAL_MS,
    );
    const nowMs = options.nowMs ?? (() => performance.now());
    const sleep =
      options.sleep ??
      ((delayMs: number) => new Promise<void>((resolve) => setTimeout(resolve, delayMs)));
    const loginDeadlineMs = nowMs() + loginTtlMs;

    while (nowMs() < loginDeadlineMs) {
      if (!(await waitForPollDelay(ctx, pollDelayMs, loginDeadlineMs, nowMs, sleep))) {
        ctx.addItem(addInfo(ctx.sessionId, "OpenAI Account login cancelled", "i"));
        return;
      }
      if (nowMs() >= loginDeadlineMs) break;

      let result: OpenAIAccountPollPayload;
      try {
        result = await ctx.request<OpenAIAccountPollPayload>(
          "openai_account.auth.poll_login",
          { login_id: login.login_id },
          AUTH_REQUEST_TIMEOUT_MS,
        );
      } catch (error) {
        if (ctx.isInterruptRequested()) {
          ctx.addItem(addInfo(ctx.sessionId, "OpenAI Account login cancelled", "i"));
          return;
        }
        if (isRetryableOpenAIAccountError(error)) continue;
        throw error;
      }

      if (result.status === "expired") break;
      if (result.status === "authenticated" || result.authenticated) {
        const authenticatedStatus = result.auth ?? { authenticated: true };
        try {
          const models = await listOpenAIAccountModels(ctx);
          await finishAuthenticatedLogin(
            ctx,
            models.auth ?? authenticatedStatus,
            models,
            requestedModel,
          );
        } catch (error) {
          showAuthStatus(ctx, authenticatedStatus);
          ctx.addItem(
            addError(
              ctx.sessionId,
              `OpenAI Account is connected, but the model catalog could not be loaded: ${errorMessage(error)}`,
            ),
          );
        }
        return;
      }
    }
    ctx.addItem(addError(ctx.sessionId, "OpenAI Account login expired; run /auth login again"));
  } catch (error) {
    if (ctx.isInterruptRequested()) {
      ctx.addItem(addInfo(ctx.sessionId, "OpenAI Account login cancelled", "i"));
    } else {
      ctx.addItem(addError(ctx.sessionId, `OpenAI Account login failed: ${errorMessage(error)}`));
    }
  } finally {
    ctx.clearInterruptRequested();
    ctx.setRunningCommand?.(null);
  }
}

async function runWithAuthError(
  ctx: CommandContext,
  label: string,
  action: () => Promise<void>,
): Promise<void> {
  try {
    await action();
  } catch (error) {
    ctx.addItem(addError(ctx.sessionId, `${label} failed: ${errorMessage(error)}`));
  }
}

export function createAuthCommand(options: LoginOptions = {}): SlashCommand {
  return {
    name: "auth",
    description: "Manage OpenAI Account authentication and models",
    usage: "/auth [status|login|models|use <model-id-or-index>|logout]",
    example: "/auth status\n/auth use gpt-5.4",
    argGuide:
      "status | login [model=<model-id-or-index>] | models | use <model-id-or-index> | logout",
    kind: CommandKind.BUILT_IN,
    takesArgs: true,
    completion: async (_ctx, partial) => {
      const options = ["status", "login", "models", "use", "logout"];
      return options.filter((option) => option.startsWith(partial.trim().toLowerCase()));
    },
    action: async (ctx, args) => {
      const [operation = "status", ...rest] = args.trim().split(/\s+/).filter(Boolean);
      const operationArgs = rest.join(" ");
      switch (operation.toLowerCase()) {
        case "status":
          await runWithAuthError(ctx, "auth status", async () => {
            const status = await getOpenAIAccountAuthStatus(ctx.request);
            try {
              const current = resolveCurrentModel(await getConfiguredModels(ctx));
              showAuthStatus(ctx, status, current);
            } catch (error) {
              showAuthStatus(ctx, status);
              ctx.addItem(
                addError(
                  ctx.sessionId,
                  `Current model could not be loaded: ${errorMessage(error)}`,
                ),
              );
            }
          });
          return;
        case "login":
          await runLogin(ctx, operationArgs, options);
          return;
        case "models":
          await runWithAuthError(ctx, "auth models", async () => {
            const catalog = await listOpenAIAccountModels(ctx);
            const configured = await getConfiguredModels(ctx);
            await promptForOpenAIAccountModel(ctx, catalog, configured);
          });
          return;
        case "use":
          await runWithAuthError(ctx, "auth use", async () => {
            const modelId = parseOpenAIAccountModelArgument(operationArgs);
            if (!modelId) throw new Error("usage: /auth use <model-id-or-index>");
            const catalog = await listOpenAIAccountModels(ctx);
            await useOpenAIAccountModel(ctx, resolveCatalogModelId(modelId, catalog), catalog);
          });
          return;
        case "logout":
          await runWithAuthError(ctx, "auth logout", async () => {
            const result = await ctx.request<{
              logged_out?: boolean;
              auth?: OpenAIAccountAuthStatus;
            }>("openai_account.auth.logout", {}, AUTH_REQUEST_TIMEOUT_MS);
            const loggedOutStatus = result.auth ?? { authenticated: false };
            try {
              const current = resolveCurrentModel(await getConfiguredModels(ctx));
              showAuthStatus(ctx, loggedOutStatus, current);
            } catch (error) {
              showAuthStatus(ctx, loggedOutStatus);
              ctx.addItem(
                addError(
                  ctx.sessionId,
                  `Current model could not be loaded: ${errorMessage(error)}`,
                ),
              );
            }
            ctx.addItem(
              addInfo(
                ctx.sessionId,
                "Saved model configurations were kept. OpenAIAccount models require /auth login before use.",
                "m",
              ),
            );
          });
          return;
        default:
          ctx.addItem(
            addError(
              ctx.sessionId,
              "usage: /auth [status|login|models|use <model-id-or-index>|logout]",
            ),
          );
      }
    },
  };
}
