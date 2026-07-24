/**
 * session.create 创建会话：加长超时，并在超时后按同一 session_id 做幂等恢复。
 *
 * 背景：前端默认 RPC 超时 15s，而 Gateway→AgentServer unary 可达 600s。
 * create 超时后 pending 被丢弃，后端却可能已落盘，导致前后端会话分叉。
 */

import type { WorkMode } from '../../features/workspace/projectTypes';

export const SESSION_CREATE_TIMEOUT_MS = 60_000;

/** 超时后轮询 metadata 的次数（含首次立即查询）。 */
export const SESSION_CREATE_METADATA_POLL_ATTEMPTS = 5;

/** 轮询间隔；给仍在飞行的 create 留出落盘时间，降低并发重试 create 的概率。 */
export const SESSION_CREATE_METADATA_POLL_INTERVAL_MS = 500;

export type SessionCreateRequestFn = <T = unknown>(
  method: string,
  params?: Record<string, unknown>,
  options?: { timeoutMs?: number },
) => Promise<T>;

export interface SessionCreatePayload {
  session_id?: string;
  sessionId?: string;
  project_id?: string;
  projectId?: string;
  project_dir?: string;
  projectDir?: string;
  work_mode?: WorkMode | string;
  workMode?: WorkMode | string;
}

export interface CreatedConversationSession {
  session_id: string;
  project_id?: string;
  project_dir?: string;
  work_mode?: WorkMode;
}

export interface CreateConversationSessionOptions {
  metadataPollAttempts?: number;
  metadataPollIntervalMs?: number;
  sleep?: (ms: number) => Promise<void>;
}

function normalizeWorkMode(value: unknown): WorkMode | undefined {
  return value === 'work' || value === 'code' ? value : undefined;
}

function errorCode(error: unknown): string | undefined {
  if (!error || typeof error !== 'object') return undefined;
  const code = (error as { code?: unknown }).code;
  return typeof code === 'string' ? code : undefined;
}

export function isRequestTimeoutError(error: unknown): boolean {
  return errorCode(error) === 'REQUEST_TIMEOUT';
}

export function isAlreadyExistsError(error: unknown): boolean {
  return errorCode(error) === 'ALREADY_EXISTS';
}

export function resolveCreatedSessionId(
  payload: SessionCreatePayload | null | undefined,
): string | undefined {
  if (!payload) return undefined;
  const direct = payload.session_id ?? payload.sessionId;
  if (typeof direct === 'string' && direct.trim()) {
    return direct.trim();
  }
  return undefined;
}

function normalizeCreatedSession(
  expectedSessionId: string,
  payload?: SessionCreatePayload | null,
): CreatedConversationSession {
  return {
    session_id: expectedSessionId,
    project_id: payload?.project_id ?? payload?.projectId,
    project_dir: payload?.project_dir ?? payload?.projectDir,
    work_mode: normalizeWorkMode(payload?.work_mode ?? payload?.workMode),
  };
}

function defaultSleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    setTimeout(resolve, ms);
  });
}

async function invokeSessionCreate(
  request: SessionCreateRequestFn,
  createParams: Record<string, unknown>,
  expectedSessionId: string,
): Promise<CreatedConversationSession> {
  const payload = await request<SessionCreatePayload>('session.create', createParams, {
    timeoutMs: SESSION_CREATE_TIMEOUT_MS,
  });
  const createdSessionId = resolveCreatedSessionId(payload);
  if (createdSessionId !== expectedSessionId) {
    throw new Error('session.create returned an unexpected session id');
  }
  return normalizeCreatedSession(expectedSessionId, payload);
}

async function tryLoadCreatedSessionMetadata(
  request: SessionCreateRequestFn,
  expectedSessionId: string,
): Promise<CreatedConversationSession | undefined> {
  try {
    const meta = await request<SessionCreatePayload>(
      'session.get_metadata',
      { session_id: expectedSessionId },
      { timeoutMs: SESSION_CREATE_TIMEOUT_MS },
    );
    const existingId = resolveCreatedSessionId(meta) ?? expectedSessionId;
    if (existingId === expectedSessionId) {
      return normalizeCreatedSession(expectedSessionId, meta);
    }
  } catch {
    // metadata 尚未就绪或查询失败时继续轮询
  }
  return undefined;
}

async function recoverAfterCreateTimeout(
  request: SessionCreateRequestFn,
  createParams: Record<string, unknown>,
  expectedSessionId: string,
  options: CreateConversationSessionOptions = {},
): Promise<CreatedConversationSession> {
  const pollAttempts = Math.max(
    1,
    options.metadataPollAttempts ?? SESSION_CREATE_METADATA_POLL_ATTEMPTS,
  );
  const pollIntervalMs = Math.max(
    0,
    options.metadataPollIntervalMs ?? SESSION_CREATE_METADATA_POLL_INTERVAL_MS,
  );
  const sleep = options.sleep ?? defaultSleep;

  for (let attempt = 0; attempt < pollAttempts; attempt += 1) {
    if (attempt > 0 && pollIntervalMs > 0) {
      await sleep(pollIntervalMs);
    }
    const recovered = await tryLoadCreatedSessionMetadata(request, expectedSessionId);
    if (recovered) {
      return recovered;
    }
  }

  // 轮询仍未见到落盘时，再同 id 重试；ALREADY_EXISTS 视为并发 create 已成功。
  try {
    return await invokeSessionCreate(request, createParams, expectedSessionId);
  } catch (retryError) {
    if (isAlreadyExistsError(retryError)) {
      const lateMeta = await tryLoadCreatedSessionMetadata(request, expectedSessionId);
      if (lateMeta) {
        return lateMeta;
      }
      return normalizeCreatedSession(expectedSessionId);
    }
    throw retryError;
  }
}

/**
 * 创建会话。超时后先轮询 metadata，再同 session_id 重试；ALREADY_EXISTS 视为成功。
 */
export async function createConversationSession(
  request: SessionCreateRequestFn,
  createParams: Record<string, unknown>,
  expectedSessionId: string,
  options: CreateConversationSessionOptions = {},
): Promise<CreatedConversationSession> {
  try {
    return await invokeSessionCreate(request, createParams, expectedSessionId);
  } catch (error) {
    if (!isRequestTimeoutError(error)) {
      throw error;
    }
    return recoverAfterCreateTimeout(request, createParams, expectedSessionId, options);
  }
}
