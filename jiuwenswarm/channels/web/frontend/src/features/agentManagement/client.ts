import { webRequest } from '../../services/webClient';
import { AgentManagementError, type AgentManagementClient } from './port';
import { getAgentManagementLocale } from './locale';
import {
  normalizeAgentFileContent,
  normalizeAgentFileTree,
  normalizeAgentTemplateDetail,
  normalizeAgentTemplateListItem,
  normalizeSkillOption,
} from './adapter';
import type {
  RawAgentDetailPayload,
  RawAgentFileListPayload,
  RawAgentFileReadPayload,
  RawAgentListPayload,
  RawSkillListPayload,
} from './raw';

export { AgentManagementError } from './port';
export type { AgentInstallResult, AgentManagementClient } from './port';

function rethrowAgentError(error: unknown): never {
  if (error instanceof AgentManagementError) {
    throw error;
  }
  if (error instanceof Error) {
    throw new AgentManagementError(error.message);
  }
  throw new AgentManagementError(String(error));
}

export function createLiveAgentManagementClient(): AgentManagementClient {
  return {
    source: 'live',
    async listCatalog() {
      try {
        const payload = await webRequest<RawAgentListPayload>('agent_templates.list', {});
        return (payload.templates || []).map((item) => normalizeAgentTemplateListItem(item, getAgentManagementLocale()));
      } catch (error) {
        return rethrowAgentError(error);
      }
    },
    async getDefinition(id) {
      try {
        const payload = await webRequest<RawAgentDetailPayload>('agent_templates.show', { id });
        if (!payload.template) {
          throw new AgentManagementError('Agent detail is empty', 'agent_detail_empty', false);
        }
        return normalizeAgentTemplateDetail(payload.template, getAgentManagementLocale());
      } catch (error) {
        return rethrowAgentError(error);
      }
    },
    async getDefinitionFiles(id) {
      try {
        const payload = await webRequest<RawAgentFileListPayload>('agent_templates.file.list', { id });
        return normalizeAgentFileTree(payload.tree);
      } catch (error) {
        return rethrowAgentError(error);
      }
    },
    async getDefinitionFile(id, relativePath) {
      try {
        const payload = await webRequest<RawAgentFileReadPayload>('agent_templates.file.read', {
          id,
          path: relativePath,
        });
        return normalizeAgentFileContent(payload);
      } catch (error) {
        return rethrowAgentError(error);
      }
    },
    async listSkillOptions() {
      try {
        const payload = await webRequest<RawSkillListPayload>('skills.list', { with_installed: true });
        return (payload.skills || []).map(normalizeSkillOption).filter((item) => item.id.length > 0);
      } catch (error) {
        return rethrowAgentError(error);
      }
    },
    async createAgent(draft) {
      try {
        await webRequest('agent_templates.create', {
          id: draft.id,
          name: draft.name,
          description: draft.description,
          persona: draft.persona,
          skills: draft.skillRefs,
          generate: false,
        });
      } catch (error) {
        return rethrowAgentError(error);
      }
    },
    async installDefinition(id) {
      try {
        await webRequest('agent_templates.install', { id });
        return { kind: 'ok' };
      } catch (error) {
        return rethrowAgentError(error);
      }
    },
    async uninstallDefinition(id) {
      try {
        await webRequest('agent_templates.uninstall', { id });
      } catch (error) {
        return rethrowAgentError(error);
      }
    },
  };
}
