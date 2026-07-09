/**
 * AvatarCenter — 数字分身中心
 */

import { useState, useEffect, useCallback } from 'react';
import { useTranslation } from 'react-i18next';
import { useAvatarStore, PersonaConfig, AvatarConfig, CodingEngine } from '../../stores/avatarStore';
import { webRequest } from '../../services/webClient';
import type { WebRequestOptions } from '../../types';
import { PlatformPageLayout, PlatformEmpty } from '../AvatarPlatform/PlatformPageLayout';
import { PersonaIcon } from '../AvatarPlatform/PersonaIcon';
import {
  CodingEngineSelect,
  CodingEngineStatusMap,
  codingEngineLabel,
  pickSelectableCodingEngine,
} from '../AvatarPlatform/CodingEngineSelect';
import { AvatarSkillsPromptConfig } from '../AvatarPlatform/AvatarSkillsPromptConfig';
import { AvatarFormModal } from '../AvatarPlatform/AvatarFormModal';
import { PersonaTemplateModal } from '../AvatarPlatform/PersonaTemplateModal';
import '../AvatarPlatform/AvatarPlatform.css';

type TabKey = 'templates' | 'my-avatars';

interface AvatarCenterProps {
  sessionId: string;
  onChatWithAvatar?: (avatarId: string) => void;
  onNavigateToConfig?: () => void;
}

export function AvatarCenter({ sessionId, onChatWithAvatar, onNavigateToConfig }: AvatarCenterProps) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<TabKey>('templates');
  const {
    personas,
    avatars,
    fetchPersonas,
    fetchAvatars,
    createAvatar,
    updateAvatar,
    deleteAvatar,
    createPersona,
    updatePersona,
    deletePersona,
    duplicatePersona,
    generatePersona,
  } = useAvatarStore();
  const [codingEngineStatus, setCodingEngineStatus] = useState<CodingEngineStatusMap>({});

  const handleChatWithAvatar = useCallback(
    (avatarId: string) => {
      onChatWithAvatar?.(avatarId);
    },
    [onChatWithAvatar],
  );

  const sendRequest = useCallback(
    (method: string, params?: Record<string, unknown>, options?: WebRequestOptions) => webRequest(method, params, options),
    [],
  );

  const fetchCodingEngineStatus = useCallback(async () => {
    try {
      const result = await sendRequest('coding.engines.status') as { engines?: CodingEngineStatusMap };
      if (result?.engines) {
        setCodingEngineStatus(result.engines);
      }
    } catch {
      setCodingEngineStatus({});
    }
  }, [sendRequest]);

  const handleRetryInstall = useCallback(async (engine: string) => {
    try {
      await sendRequest('coding.cli.retry_install', { engine_kind: engine });
      setTimeout(() => fetchCodingEngineStatus(), 1000);
    } catch (err) {
      console.error('Failed to retry install:', err);
    }
  }, [sendRequest, fetchCodingEngineStatus]);

  useEffect(() => {
    fetchPersonas(sendRequest);
    fetchAvatars(sendRequest);
    fetchCodingEngineStatus();
  }, [fetchPersonas, fetchAvatars, fetchCodingEngineStatus, sendRequest]);

  useEffect(() => {
    fetchCodingEngineStatus();
  }, [activeTab, fetchCodingEngineStatus]);

  const workflow = [
    { num: 1, label: t('platform.workflow.createAvatar', '创建分身'), active: true },
    { num: 2, label: t('platform.workflow.setupTrigger', '配置触发器') },
    { num: 3, label: t('platform.workflow.viewReport', '查看报告') },
  ];

  return (
    <PlatformPageLayout
      title={t('avatar.pageTitle', '数字分身')}
      subtitle={t('avatar.pageSubtitle', '选择角色模板创建分身，绑定 AIDLC 技能与编码后端，由触发器驱动自主执行任务。')}
      workflow={workflow}
      toolbar={
        <>
          <div className="avatar-platform__tabs">
            <button
              type="button"
              className={`avatar-platform__tab${activeTab === 'templates' ? ' avatar-platform__tab--active' : ''}`}
              onClick={() => setActiveTab('templates')}
            >
              {t('avatar.templates', '分身模板库')}
            </button>
            <button
              type="button"
              className={`avatar-platform__tab${activeTab === 'my-avatars' ? ' avatar-platform__tab--active' : ''}`}
              onClick={() => setActiveTab('my-avatars')}
            >
              {t('avatar.myAvatars', '我的分身')}
              {avatars.length > 0 && ` (${avatars.length})`}
            </button>
          </div>
        </>
      }
    >
      {activeTab === 'templates' ? (
        <PersonaTemplateList
          personas={personas}
          avatars={avatars}
          sessionId={sessionId}
          sendRequest={sendRequest}
          createAvatar={createAvatar}
          createPersona={createPersona}
          updatePersona={updatePersona}
          deletePersona={deletePersona}
          duplicatePersona={duplicatePersona}
          generatePersona={generatePersona}
          codingEngineStatus={codingEngineStatus}
          onRetryInstall={handleRetryInstall}
          onGoToConfig={onNavigateToConfig}
        />
      ) : (
        <MyAvatarsList
          avatars={avatars}
          personas={personas}
          sessionId={sessionId}
          sendRequest={sendRequest}
          updateAvatar={updateAvatar}
          deleteAvatar={deleteAvatar}
          onSelectAvatar={handleChatWithAvatar}
          codingEngineStatus={codingEngineStatus}
          onRetryInstall={handleRetryInstall}
          onGoToConfig={onNavigateToConfig}
        />
      )}
    </PlatformPageLayout>
  );
}

function PersonaTemplateList({
  personas,
  avatars,
  createAvatar,
  createPersona,
  updatePersona,
  deletePersona,
  duplicatePersona,
  generatePersona,
  sessionId,
  sendRequest,
  codingEngineStatus,
  onRetryInstall,
  onGoToConfig,
}: {
  personas: PersonaConfig[];
  avatars: AvatarConfig[];
  sessionId: string;
  createAvatar: ReturnType<typeof useAvatarStore.getState>['createAvatar'];
  createPersona: ReturnType<typeof useAvatarStore.getState>['createPersona'];
  updatePersona: ReturnType<typeof useAvatarStore.getState>['updatePersona'];
  deletePersona: ReturnType<typeof useAvatarStore.getState>['deletePersona'];
  duplicatePersona: ReturnType<typeof useAvatarStore.getState>['duplicatePersona'];
  generatePersona: ReturnType<typeof useAvatarStore.getState>['generatePersona'];
  sendRequest: (method: string, params?: Record<string, unknown>, options?: WebRequestOptions) => Promise<unknown>;
  codingEngineStatus: CodingEngineStatusMap;
  onRetryInstall: (engine: string) => void;
  onGoToConfig?: () => void;
}) {
  const { t } = useTranslation();
  const [modalPersona, setModalPersona] = useState<PersonaConfig | null>(null);
  const [templateModal, setTemplateModal] = useState<{ mode: 'create' | 'edit'; persona?: PersonaConfig } | null>(null);
  const handleDeletePersonaTemplate = async (persona: PersonaConfig) => {
    const linkedAvatars = avatars.filter((avatar) => avatar.persona_id === persona.id);
    const message = linkedAvatars.length > 0
      ? t(
        'avatar.personaTemplate.confirmDeleteWithAvatars',
        '当前已基于该模板创建 {{count}} 个数字分身实例，是否一起删除？',
        { count: linkedAvatars.length },
      )
      : t('avatar.personaTemplate.confirmDelete', '确认删除该自定义模板？');
    if (!window.confirm(message)) return;
    try {
      await deletePersona(sendRequest, persona.id, { cascadeAvatars: linkedAvatars.length > 0 });
    } catch (err) {
      window.alert(err instanceof Error ? err.message : String(err));
    }
  };

  if (personas.length === 0) {
    return (
      <>
        <div className="avatar-template-toolbar">
          <button type="button" className="avatar-platform__btn avatar-platform__btn--primary" onClick={() => setTemplateModal({ mode: 'create' })}>
            {t('avatar.personaTemplate.new', '新建模板')}
          </button>
        </div>
        <PlatformEmpty
          title={t('avatar.noTemplates', '暂无分身模板')}
          description={t('avatar.noTemplatesHint', '内置模板将自动加载，请确认后端服务已连接。')}
        />
        {templateModal && (
          <PersonaTemplateModal
            mode={templateModal.mode}
            persona={templateModal.persona}
            sessionId={sessionId}
            sendRequest={sendRequest}
            onGenerate={(prompt) => generatePersona(sendRequest, prompt)}
            onClose={() => setTemplateModal(null)}
            onSave={async (persona) => {
              await createPersona(sendRequest, persona);
              setTemplateModal(null);
            }}
          />
        )}
      </>
    );
  }

  return (
    <>
      <div className="avatar-template-toolbar">
        <button type="button" className="avatar-platform__btn avatar-platform__btn--primary" onClick={() => setTemplateModal({ mode: 'create' })}>
          {t('avatar.personaTemplate.new', '新建模板')}
        </button>
      </div>
      <div className="avatar-platform__grid">
        {personas.map((persona) => {
          const owned = avatars.some((a) => a.persona_id === persona.id);
          return (
            <div key={persona.id} className="avatar-platform__card">
              <div className="avatar-platform__card-header">
                <div className="avatar-platform__card-identity">
                  <PersonaIcon icon={persona.icon} />
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="avatar-platform__card-title">{persona.display_name}</h3>
                      {!persona.builtin && (
                        <span className="avatar-platform__tag avatar-platform__tag--custom">
                          {t('avatar.personaTemplate.customBadge', '自定义')}
                        </span>
                      )}
                    </div>
                    <p className="avatar-platform__card-meta">v{persona.version}{owned ? ` · ${t('avatar.alreadyCreated', '已创建')}` : ''}</p>
                  </div>
                </div>
              </div>
              <p className="avatar-platform__card-desc">{persona.description}</p>
              {persona.tags.length > 0 && (
                <div className="avatar-platform__tags">
                  {persona.tags.map((tag) => (
                    <span key={tag} className="avatar-platform__tag">{tag}</span>
                  ))}
                </div>
              )}
              {persona.skills.length > 0 && (
                <div className="avatar-platform__skills">
                  {persona.skills.slice(0, 6).map((skill) => (
                    <span key={skill} className="avatar-platform__skill">{skill}</span>
                  ))}
                  {persona.skills.length > 6 && (
                    <span className="avatar-platform__skill">+{persona.skills.length - 6}</span>
                  )}
                </div>
              )}
              <div className="avatar-platform__card-footer">
                <span className="avatar-platform__tag avatar-platform__tag--muted">
                  {persona.trigger_templates.length} {t('avatar.triggerCount', '个触发器模板')}
                </span>
                <div className="avatar-template-card-actions">
                  <button
                    type="button"
                    className="avatar-platform__btn avatar-platform__btn--ghost"
                    onClick={async () => {
                      const newId = window.prompt(
                        t('avatar.personaTemplate.copyIdPrompt', '请输入新模板 ID'),
                        `${persona.id}-custom`,
                      );
                      if (!newId) return;
                      await duplicatePersona(sendRequest, persona.id, newId.trim(), `${persona.display_name} 副本`);
                    }}
                  >
                    {t('avatar.personaTemplate.copy', '复制')}
                  </button>
                  {!persona.builtin && (
                    <>
                      <button type="button" className="avatar-platform__btn avatar-platform__btn--ghost" onClick={() => setTemplateModal({ mode: 'edit', persona })}>
                        {t('common.edit', '编辑')}
                      </button>
                      <button
                        type="button"
                        className="avatar-platform__btn avatar-platform__btn--danger"
                        onClick={async () => {
                          await handleDeletePersonaTemplate(persona);
                        }}
                      >
                        {t('avatar.delete', '删除')}
                      </button>
                    </>
                  )}
                  <button type="button" className="avatar-platform__btn avatar-platform__btn--primary" onClick={() => setModalPersona(persona)}>
                    {t('avatar.create', '创建分身')}
                  </button>
                </div>
              </div>
            </div>
          );
        })}
      </div>
      {modalPersona && (
        <CreateAvatarModal
          persona={modalPersona}
          sessionId={sessionId}
          codingEngineStatus={codingEngineStatus}
          onRetryInstall={onRetryInstall}
          onGoToConfig={onGoToConfig}
          onClose={() => setModalPersona(null)}
          onCreate={async (params) => {
            await createAvatar(sendRequest, params);
            setModalPersona(null);
          }}
        />
      )}
      {templateModal && (
        <PersonaTemplateModal
          mode={templateModal.mode}
          persona={templateModal.persona}
          sessionId={sessionId}
          sendRequest={sendRequest}
          onGenerate={(prompt) => generatePersona(sendRequest, prompt)}
          onClose={() => setTemplateModal(null)}
          onSave={async (persona) => {
            if (templateModal.mode === 'edit' && templateModal.persona) {
              await updatePersona(sendRequest, templateModal.persona.id, persona);
            } else {
              await createPersona(sendRequest, persona);
            }
            setTemplateModal(null);
          }}
        />
      )}
    </>
  );
}

function CreateAvatarModal({
  persona,
  sessionId,
  codingEngineStatus,
  onRetryInstall,
  onGoToConfig,
  onClose,
  onCreate,
}: {
  persona: PersonaConfig;
  sessionId: string;
  codingEngineStatus: CodingEngineStatusMap;
  onRetryInstall?: (engine: string) => void;
  onGoToConfig?: () => void;
  onClose: () => void;
  onCreate: (params: {
    persona_id: string;
    name?: string;
    coding_engine?: CodingEngine;
    system_prompt?: string;
    extra_skills?: string[];
  }) => Promise<void>;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState(persona.display_name);
  const engines = (persona.coding_engines || []) as CodingEngine[];
  const [codingEngine, setCodingEngine] = useState<CodingEngine>(() =>
    pickSelectableCodingEngine(
      engines,
      codingEngineStatus,
      (persona.default_coding_engine as CodingEngine) || engines[0] || 'jiuwen-coding',
    ),
  );
  const [selectedSkills, setSelectedSkills] = useState<string[]>(() => [...persona.skills]);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const params: {
        persona_id: string;
        name?: string;
        coding_engine?: CodingEngine;
        system_prompt?: string;
        extra_skills?: string[];
      } = {
        persona_id: persona.id,
        name: name.trim() || undefined,
        coding_engine: persona.coding_capable ? codingEngine : undefined,
      };
      const trimmedPrompt = systemPrompt.trim();
      if (trimmedPrompt && trimmedPrompt !== persona.system_prompt.trim()) {
        params.system_prompt = trimmedPrompt;
      }
      const extraSkills = selectedSkills.filter((s) => !persona.skills.includes(s));
      if (extraSkills.length > 0) {
        params.extra_skills = extraSkills;
      }
      await onCreate(params);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <AvatarFormModal
      title={t('avatar.createTitle', '创建分身')}
      subtitle={persona.description}
      personaIcon={persona.icon}
      onClose={onClose}
      error={error}
      footer={
        <>
          <button type="button" className="avatar-platform__btn avatar-platform__btn--ghost" onClick={onClose}>
            {t('common.cancel', '取消')}
          </button>
          <button
            type="button"
            className="avatar-platform__btn avatar-platform__btn--primary"
            disabled={submitting}
            onClick={handleSubmit}
          >
            {submitting ? t('avatar.creating', '创建中...') : t('avatar.create', '创建分身')}
          </button>
        </>
      }
    >
      <section className="avatar-form-section avatar-form-section--compact">
        <div className="avatar-form-section__head">
          <div>
            <h4 className="avatar-form-section__title">{t('avatar.nameLabel', '分身名称')}</h4>
            <p className="avatar-form-section__hint">{t('avatar.nameHint', '用于在列表和对话中识别该分身。')}</p>
          </div>
        </div>
        <div className="avatar-form-section__body">
          <input
            className="avatar-platform__input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder={persona.display_name}
          />
        </div>
      </section>

      {persona.coding_capable && engines.length > 0 && (
        <section className="avatar-form-section avatar-form-section--compact">
          <div className="avatar-form-section__body avatar-form-section__body--flush">
            <CodingEngineSelect
              value={codingEngine}
              options={engines}
              onChange={setCodingEngine}
              engineStatus={codingEngineStatus}
              onRetryInstall={onRetryInstall}
              onGoToConfig={onGoToConfig ? () => { onClose(); onGoToConfig(); } : undefined}
            />
          </div>
        </section>
      )}

      <AvatarSkillsPromptConfig
        sessionId={sessionId}
        persona={persona}
        selectedSkills={selectedSkills}
        onSkillsChange={setSelectedSkills}
        systemPrompt={systemPrompt}
        onSystemPromptChange={setSystemPrompt}
      />
    </AvatarFormModal>
  );
}

function MyAvatarsList({
  avatars,
  personas,
  updateAvatar,
  deleteAvatar,
  sendRequest,
  sessionId,
  onSelectAvatar,
  codingEngineStatus,
  onRetryInstall,
  onGoToConfig,
}: {
  avatars: AvatarConfig[];
  personas: PersonaConfig[];
  sessionId: string;
  updateAvatar: ReturnType<typeof useAvatarStore.getState>['updateAvatar'];
  deleteAvatar: ReturnType<typeof useAvatarStore.getState>['deleteAvatar'];
  sendRequest: (method: string, params?: Record<string, unknown>, options?: WebRequestOptions) => Promise<unknown>;
  onSelectAvatar: (id: string) => void;
  codingEngineStatus: CodingEngineStatusMap;
  onRetryInstall: (engine: string) => void;
  onGoToConfig?: () => void;
}) {
  const { t } = useTranslation();
  const [editAvatar, setEditAvatar] = useState<AvatarConfig | null>(null);

  const getPersona = (personaId: string) => personas.find((p) => p.id === personaId);

  if (avatars.length === 0) {
    return (
      <PlatformEmpty
        title={t('avatar.noAvatars', '还没有创建分身')}
        description={t('avatar.noAvatarsHint', '从「分身模板库」选择一个角色（Committer / 开发 / 测试），一键创建你的数字分身。')}
      />
    );
  }

  const statusClass = (status: string) => {
    if (status === 'running') return 'avatar-platform__status--running';
    if (status === 'error') return 'avatar-platform__status--error';
    return 'avatar-platform__status--idle';
  };

  const statusLabel = (status: string) => {
    if (status === 'running') return t('avatar.statusRunning', '运行中');
    if (status === 'error') return t('avatar.statusError', '异常');
    return t('avatar.statusIdle', '空闲');
  };

  return (
    <>
      <div className="avatar-platform__list">
        {avatars.map((avatar) => {
          const persona = getPersona(avatar.persona_id);
          return (
            <div key={avatar.id} className="avatar-platform__card">
              <div className="avatar-platform__card-header">
                <div className="avatar-platform__card-identity">
                  <PersonaIcon icon={persona?.icon || 'avatar'} size="sm" />
                  <div>
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="avatar-platform__card-title">{avatar.name}</h3>
                      <span className={`avatar-platform__status ${statusClass(avatar.status)}`}>{statusLabel(avatar.status)}</span>
                    </div>
                    <p className="avatar-platform__card-meta">
                      {persona?.display_name || avatar.persona_id}
                      {' · '}{t('avatar.createdAt', '创建于')} {new Date(avatar.created_at).toLocaleDateString()}
                      {avatar.coding_engine && persona?.coding_capable && (
                        <> · {codingEngineLabel(avatar.coding_engine, t)}</>
                      )}
                    </p>
                  </div>
                </div>
                <div className="flex gap-2 shrink-0">
                  <button type="button" className="avatar-platform__btn avatar-platform__btn--primary" onClick={() => onSelectAvatar(avatar.id)}>
                    {t('avatar.chat', '对话')}
                  </button>
                  <button type="button" className="avatar-platform__btn avatar-platform__btn--ghost" onClick={() => setEditAvatar(avatar)}>
                    {t('avatar.configure', '配置')}
                  </button>
                  <button
                    type="button"
                    className="avatar-platform__btn avatar-platform__btn--danger"
                    onClick={async () => {
                      if (!window.confirm(t('avatar.confirmDelete', '确认删除该分身？'))) return;
                      await deleteAvatar(sendRequest, avatar.id);
                    }}
                  >
                    {t('avatar.delete', '删除')}
                  </button>
                </div>
              </div>
              {avatar.skills.length > 0 && (
                <div className="avatar-platform__skills">
                  {avatar.skills.map((skill) => (
                    <span key={skill} className="avatar-platform__skill">{skill}</span>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
      {editAvatar && (
        <EditAvatarModal
          avatar={editAvatar}
          persona={getPersona(editAvatar.persona_id)}
          sessionId={sessionId}
          codingEngineStatus={codingEngineStatus}
          onRetryInstall={onRetryInstall}
          onGoToConfig={onGoToConfig}
          onClose={() => setEditAvatar(null)}
          onSave={async (updates) => {
            await updateAvatar(sendRequest, editAvatar.id, updates);
            setEditAvatar(null);
          }}
        />
      )}
    </>
  );
}

function EditAvatarModal({
  avatar,
  persona,
  sessionId,
  codingEngineStatus,
  onRetryInstall,
  onGoToConfig,
  onClose,
  onSave,
}: {
  avatar: AvatarConfig;
  persona?: PersonaConfig;
  sessionId: string;
  codingEngineStatus: CodingEngineStatusMap;
  onRetryInstall?: (engine: string) => void;
  onGoToConfig?: () => void;
  onClose: () => void;
  onSave: (updates: Record<string, unknown>) => Promise<void>;
}) {
  const { t } = useTranslation();
  const engines = (persona?.coding_engines || []) as CodingEngine[];
  const [codingEngine, setCodingEngine] = useState<CodingEngine>(() =>
    pickSelectableCodingEngine(
      engines,
      codingEngineStatus,
      (avatar.coding_engine as CodingEngine) || engines[0] || 'jiuwen-coding',
    ),
  );
  const [selectedSkills, setSelectedSkills] = useState<string[]>(() => [...avatar.skills]);
  const [systemPrompt, setSystemPrompt] = useState(() => avatar.system_prompt ?? '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!persona) {
    return null;
  }

  const defaultPrompt = persona.system_prompt.trim();

  return (
    <AvatarFormModal
      title={t('avatar.editTitle', '分身配置')}
      subtitle={`${avatar.name} · ${persona.display_name}`}
      personaIcon={persona.icon}
      onClose={onClose}
      error={error}
      footer={
        <>
          <button type="button" className="avatar-platform__btn avatar-platform__btn--ghost" onClick={onClose}>
            {t('common.cancel', '取消')}
          </button>
          <button
            type="button"
            className="avatar-platform__btn avatar-platform__btn--primary"
            disabled={saving}
            onClick={async () => {
              setSaving(true);
              setError(null);
              try {
                const updates: Record<string, unknown> = {
                  skills: selectedSkills,
                };
                const trimmedPrompt = systemPrompt.trim();
                if (trimmedPrompt !== (avatar.system_prompt ?? defaultPrompt)) {
                  updates.system_prompt = trimmedPrompt;
                }
                if (persona.coding_capable) {
                  updates.coding_engine = codingEngine;
                }
                await onSave(updates);
              } catch (err) {
                setError(err instanceof Error ? err.message : String(err));
              } finally {
                setSaving(false);
              }
            }}
          >
            {t('common.save', '保存')}
          </button>
        </>
      }
    >
      {persona.coding_capable && engines.length > 0 && (
        <section className="avatar-form-section avatar-form-section--compact">
          <div className="avatar-form-section__body avatar-form-section__body--flush">
            <CodingEngineSelect
              value={codingEngine}
              options={engines}
              onChange={setCodingEngine}
              engineStatus={codingEngineStatus}
              onRetryInstall={onRetryInstall}
              onGoToConfig={onGoToConfig ? () => { onClose(); onGoToConfig(); } : undefined}
            />
          </div>
        </section>
      )}

      <AvatarSkillsPromptConfig
        sessionId={sessionId}
        persona={persona}
        selectedSkills={selectedSkills}
        onSkillsChange={setSelectedSkills}
        systemPrompt={systemPrompt}
        onSystemPromptChange={setSystemPrompt}
        allowRemovePersonaSkills
      />
    </AvatarFormModal>
  );
}
