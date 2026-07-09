/**
 * Avatar 数字分身状态管理
 */

import { create } from 'zustand';
import type { WebRequestOptions } from '../types';

const PERSONA_GENERATE_TIMEOUT_MS = 90_000;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface PersonaTriggerTemplate {
  name: string;
  type: 'cron' | 'heartbeat' | 'webhook' | 'event';
  cron_expr?: string;
  interval_seconds?: number;
  active_hours?: Record<string, string>;
  webhook_path?: string;
  event_source?: string;
  event_type?: string;
  prompt: string;
}

export interface PersonaReportSection {
  name: string;
  fields: string[];
}

export interface PersonaReportTemplate {
  title: string;
  sections: PersonaReportSection[];
}

export type CodingEngine = 'jiuwen-coding' | 'claude-code' | 'codex';

export interface PersonaConfig {
  id: string;
  display_name: string;
  description: string;
  icon: string;
  version: string;
  coding_capable?: boolean;
  coding_engines?: CodingEngine[];
  default_coding_engine?: CodingEngine | null;
  skills: string[];
  trigger_templates: PersonaTriggerTemplate[];
  system_prompt: string;
  report_template: PersonaReportTemplate;
  tags: string[];
  builtin: boolean;
}

export type AvatarStatus = 'idle' | 'running' | 'error';

export interface AvatarConfig {
  id: string;
  name: string;
  persona_id: string;
  persona_version: string;
  status: AvatarStatus;
  skills: string[];
  coding_engine?: CodingEngine | null;
  system_prompt: string | null;
  trigger_ids: string[];
  report_channels: string[];
  created_at: string;
  updated_at: string;
  extra: Record<string, unknown>;
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

interface AvatarState {
  // Persona templates
  personas: PersonaConfig[];
  personasLoading: boolean;

  // Avatar instances
  avatars: AvatarConfig[];
  avatarsLoading: boolean;

  // Current selected avatar for chat
  currentAvatarId: string | null;

  // Avatar detail panel
  detailAvatarId: string | null;
}

interface AvatarActions {
  // Persona
  fetchPersonas: (sendRequest: (method: string, params?: Record<string, unknown>) => Promise<unknown>) => Promise<void>;
  createPersona: (
    sendRequest: (method: string, params?: Record<string, unknown>) => Promise<unknown>,
    persona: PersonaConfig,
  ) => Promise<PersonaConfig | null>;
  updatePersona: (
    sendRequest: (method: string, params?: Record<string, unknown>) => Promise<unknown>,
    personaId: string,
    persona: PersonaConfig,
  ) => Promise<PersonaConfig | null>;
  deletePersona: (
    sendRequest: (method: string, params?: Record<string, unknown>) => Promise<unknown>,
    personaId: string,
    options?: { cascadeAvatars?: boolean },
  ) => Promise<void>;
  duplicatePersona: (
    sendRequest: (method: string, params?: Record<string, unknown>) => Promise<unknown>,
    sourceId: string,
    newId: string,
    displayName?: string,
  ) => Promise<PersonaConfig | null>;
  generatePersona: (
    sendRequest: (method: string, params?: Record<string, unknown>, options?: WebRequestOptions) => Promise<unknown>,
    prompt: string,
  ) => Promise<PersonaConfig | null>;

  // Avatar
  fetchAvatars: (sendRequest: (method: string, params?: Record<string, unknown>) => Promise<unknown>) => Promise<void>;
  createAvatar: (
    sendRequest: (method: string, params?: Record<string, unknown>) => Promise<unknown>,
    params: { persona_id: string; name?: string; system_prompt?: string; extra_skills?: string[]; report_channels?: string[]; coding_engine?: CodingEngine },
  ) => Promise<AvatarConfig | null>;
  updateAvatar: (
    sendRequest: (method: string, params?: Record<string, unknown>) => Promise<unknown>,
    avatarId: string,
    updates: Record<string, unknown>,
  ) => Promise<void>;
  deleteAvatar: (
    sendRequest: (method: string, params?: Record<string, unknown>) => Promise<unknown>,
    avatarId: string,
  ) => Promise<void>;

  // Selection
  setCurrentAvatarId: (id: string | null) => void;
  setDetailAvatarId: (id: string | null) => void;

  // Helpers
  getPersonaById: (id: string) => PersonaConfig | undefined;
  getAvatarById: (id: string) => AvatarConfig | undefined;
}

const CURRENT_AVATAR_STORAGE_KEY = 'jiuwenavatar.currentAvatarId';

function readStoredAvatarId(): string | null {
  try {
    return localStorage.getItem(CURRENT_AVATAR_STORAGE_KEY);
  } catch {
    return null;
  }
}

function writeStoredAvatarId(id: string | null): void {
  try {
    if (id) {
      localStorage.setItem(CURRENT_AVATAR_STORAGE_KEY, id);
    } else {
      localStorage.removeItem(CURRENT_AVATAR_STORAGE_KEY);
    }
  } catch {
    /* ignore */
  }
}

export const useAvatarStore = create<AvatarState & AvatarActions>((set, get) => ({
  // State
  personas: [],
  personasLoading: false,
  avatars: [],
  avatarsLoading: false,
  currentAvatarId: readStoredAvatarId(),
  detailAvatarId: null,

  // Persona actions
  fetchPersonas: async (sendRequest) => {
    set({ personasLoading: true });
    try {
      const result = await sendRequest('personas.list') as { personas?: PersonaConfig[] };
      set({ personas: result?.personas || [], personasLoading: false });
    } catch {
      set({ personasLoading: false });
    }
  },

  createPersona: async (sendRequest, persona) => {
    const result = await sendRequest('personas.create', { persona }) as { persona?: PersonaConfig };
    if (result?.persona) {
      set((state) => ({ personas: [...state.personas, result.persona!] }));
      return result.persona;
    }
    return null;
  },

  updatePersona: async (sendRequest, personaId, persona) => {
    const result = await sendRequest('personas.update', { persona_id: personaId, persona }) as { persona?: PersonaConfig };
    if (result?.persona) {
      set((state) => ({
        personas: state.personas.map((p) => (p.id === personaId ? result.persona! : p)),
      }));
      return result.persona;
    }
    return null;
  },

  deletePersona: async (sendRequest, personaId, options) => {
    const result = await sendRequest('personas.delete', {
      persona_id: personaId,
      cascade_avatars: !!options?.cascadeAvatars,
    }) as { success?: boolean; deleted_avatar_ids?: string[]; error?: string };
    if (result?.error || result?.success === false) {
      throw new Error(result.error || '删除 Persona 模板失败');
    }
    const deletedAvatarIds = new Set(result?.deleted_avatar_ids || []);
    set((state) => ({
      personas: state.personas.filter((p) => p.id !== personaId),
      avatars: deletedAvatarIds.size > 0
        ? state.avatars.filter((a) => !deletedAvatarIds.has(a.id))
        : state.avatars,
      currentAvatarId: state.currentAvatarId && deletedAvatarIds.has(state.currentAvatarId) ? null : state.currentAvatarId,
      detailAvatarId: state.detailAvatarId && deletedAvatarIds.has(state.detailAvatarId) ? null : state.detailAvatarId,
    }));
  },

  duplicatePersona: async (sendRequest, sourceId, newId, displayName) => {
    const result = await sendRequest('personas.duplicate', {
      source_id: sourceId,
      new_id: newId,
      display_name: displayName,
    }) as { persona?: PersonaConfig };
    if (result?.persona) {
      set((state) => ({ personas: [...state.personas, result.persona!] }));
      return result.persona;
    }
    return null;
  },

  generatePersona: async (sendRequest, prompt) => {
    const result = await sendRequest(
      'personas.generate',
      { prompt },
      { timeoutMs: PERSONA_GENERATE_TIMEOUT_MS },
    ) as { persona?: PersonaConfig };
    return result?.persona || null;
  },

  // Avatar actions
  fetchAvatars: async (sendRequest) => {
    set({ avatarsLoading: true });
    try {
      const result = await sendRequest('avatars.list') as { avatars?: AvatarConfig[] };
      set({ avatars: result?.avatars || [], avatarsLoading: false });
    } catch {
      set({ avatarsLoading: false });
    }
  },

  createAvatar: async (sendRequest, params) => {
    const result = await sendRequest('avatars.create', params) as { avatar?: AvatarConfig; error?: string };
    if (result?.avatar) {
      set((state) => ({ avatars: [...state.avatars, result.avatar!] }));
      return result.avatar;
    }
    return null;
  },

  updateAvatar: async (sendRequest, avatarId, updates) => {
    const result = await sendRequest('avatars.update', { avatar_id: avatarId, ...updates }) as { avatar?: AvatarConfig };
    if (result?.avatar) {
      set((state) => ({
        avatars: state.avatars.map((a) => (a.id === avatarId ? result.avatar! : a)),
      }));
    }
  },

  deleteAvatar: async (sendRequest, avatarId) => {
    try {
      await sendRequest('avatars.delete', { avatar_id: avatarId });
      set((state) => ({
        avatars: state.avatars.filter((a) => a.id !== avatarId),
        currentAvatarId: state.currentAvatarId === avatarId ? null : state.currentAvatarId,
        detailAvatarId: state.detailAvatarId === avatarId ? null : state.detailAvatarId,
      }));
    } catch { /* ignore */ }
  },

  // Selection
  setCurrentAvatarId: (id) => {
    writeStoredAvatarId(id);
    set({ currentAvatarId: id });
  },
  setDetailAvatarId: (id) => set({ detailAvatarId: id }),

  // Helpers
  getPersonaById: (id) => get().personas.find((p) => p.id === id),
  getAvatarById: (id) => get().avatars.find((a) => a.id === id),
}));
