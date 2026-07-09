/**
 * Avatar 技能与系统提示配置 — 创建/编辑分身时复用。
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { ChevronDown, Download, MessageSquareText, Plus, Sparkles, Wrench, X } from 'lucide-react';
import { webRequest } from '../../services/webClient';
import { PersonaConfig } from '../../stores/avatarStore';
import { TeamSkillsHubModal } from '../../features/TeamSkillsHubModal';

type SkillListItem = {
  name: string;
  description?: string;
  source?: string;
};

type InstalledPluginItem = {
  plugin_name?: string;
  marketplace?: string;
  skills?: string[];
  enabled?: boolean;
};

interface AvatarSkillsPromptConfigProps {
  sessionId: string;
  persona: PersonaConfig;
  selectedSkills: string[];
  onSkillsChange: (skills: string[]) => void;
  systemPrompt: string;
  onSystemPromptChange: (prompt: string) => void;
  /** 编辑模式允许移除 Persona 默认技能；创建模式仅展示为继承项 */
  allowRemovePersonaSkills?: boolean;
}

export function AvatarSkillsPromptConfig({
  sessionId,
  persona,
  selectedSkills,
  onSkillsChange,
  systemPrompt,
  onSystemPromptChange,
  allowRemovePersonaSkills = false,
}: AvatarSkillsPromptConfigProps) {
  const { t } = useTranslation();
  const [installedSkills, setInstalledSkills] = useState<SkillListItem[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [hubOpen, setHubOpen] = useState(false);
  const [showDefaultPrompt, setShowDefaultPrompt] = useState(false);

  const personaSkillSet = useMemo(() => new Set(persona.skills), [persona.skills]);
  const selectedSet = useMemo(() => new Set(selectedSkills), [selectedSkills]);
  const defaultPrompt = persona.system_prompt?.trim() || '';

  const fetchInstalledSkills = useCallback(async () => {
    if (!sessionId || sessionId === 'new') return;
    setSkillsLoading(true);
    try {
      const data = await webRequest<{ skills?: SkillListItem[]; plugins?: InstalledPluginItem[] }>('skills.list', {
        session_id: sessionId,
        with_installed: true,
      });
      const byName = new Map<string, SkillListItem>();
      for (const skill of data.skills || []) {
        if (skill.name) byName.set(skill.name, skill);
      }
      for (const plugin of data.plugins || []) {
        if (plugin.enabled === false) continue;
        const source = plugin.marketplace || 'installed';
        for (const name of plugin.skills || []) {
          if (!name || byName.has(name)) continue;
          byName.set(name, { name, source });
        }
        const pluginName = plugin.plugin_name;
        if (pluginName && !byName.has(pluginName)) {
          byName.set(pluginName, { name: pluginName, source });
        }
      }
      setInstalledSkills([...byName.values()]);
    } catch {
      setInstalledSkills([]);
    } finally {
      setSkillsLoading(false);
    }
  }, [sessionId]);

  useEffect(() => {
    void fetchInstalledSkills();
  }, [fetchInstalledSkills]);

  const addableSkills = useMemo(() => {
    return installedSkills
      .filter((s) => s.name && !selectedSet.has(s.name))
      .sort((a, b) => a.name.localeCompare(b.name));
  }, [installedSkills, selectedSet]);

  const handleAddSkill = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed || selectedSet.has(trimmed)) return;
    onSkillsChange([...selectedSkills, trimmed]);
  };

  const handleRemoveSkill = (name: string) => {
    if (!allowRemovePersonaSkills && personaSkillSet.has(name)) return;
    onSkillsChange(selectedSkills.filter((s) => s !== name));
  };

  const handleHubInstalled = async (skillName: string) => {
    handleAddSkill(skillName);
    await fetchInstalledSkills();
  };

  const hubDisabled = !sessionId || sessionId === 'new';
  const addPlaceholder = skillsLoading
    ? t('avatar.skills.loading', '加载已安装技能…')
    : addableSkills.length === 0
      ? t('avatar.skills.noAddable', '暂无可添加的已安装技能')
      : t('avatar.skills.addFromInstalled', '从已安装技能添加…');

  return (
    <>
      <section className="avatar-form-section">
        <div className="avatar-form-section__head">
          <span className="avatar-form-section__icon" aria-hidden>
            <MessageSquareText size={16} strokeWidth={2} />
          </span>
          <div>
            <h4 className="avatar-form-section__title">{t('avatar.systemPrompt.label', '系统提示')}</h4>
            <p className="avatar-form-section__hint">
              {t('avatar.systemPrompt.hint', '留空则使用模板默认提示；可在此追加或覆盖分身行为说明。')}
            </p>
          </div>
        </div>

        <div className="avatar-form-section__body">
          {defaultPrompt && (
            <div className="avatar-prompt-default">
              <button
                type="button"
                className="avatar-prompt-default__toggle"
                onClick={() => setShowDefaultPrompt((v) => !v)}
                aria-expanded={showDefaultPrompt}
              >
                <Sparkles size={14} />
                {t('avatar.systemPrompt.viewDefault', '查看模板默认提示')}
                <ChevronDown
                  size={14}
                  className={`avatar-prompt-default__chevron${showDefaultPrompt ? ' avatar-prompt-default__chevron--open' : ''}`}
                />
              </button>
              {showDefaultPrompt && (
                <pre className="avatar-prompt-default__preview">{defaultPrompt}</pre>
              )}
            </div>
          )}

          <textarea
            className="avatar-platform__textarea avatar-form-textarea"
            rows={5}
            value={systemPrompt}
            onChange={(e) => onSystemPromptChange(e.target.value)}
            placeholder={
              defaultPrompt
                ? t('avatar.systemPrompt.placeholderOverride', '留空使用模板默认；在此输入可覆盖…')
                : t('avatar.systemPrompt.placeholder', '使用模板默认系统提示')
            }
          />
        </div>
      </section>

      <section className="avatar-form-section">
        <div className="avatar-form-section__head">
          <span className="avatar-form-section__icon" aria-hidden>
            <Wrench size={16} strokeWidth={2} />
          </span>
          <div className="avatar-form-section__head-text">
            <div className="avatar-form-section__title-row">
              <h4 className="avatar-form-section__title">{t('avatar.skills.label', '绑定技能')}</h4>
              <span className="avatar-form-section__count">{selectedSkills.length}</span>
            </div>
            <p className="avatar-form-section__hint">
              {t('avatar.skills.hint', '分身对话与自动任务将加载以下技能；可从已安装技能或 Team Skills Hub 添加。')}
            </p>
          </div>
        </div>

        <div className="avatar-form-section__body">
          {selectedSkills.length > 0 ? (
            <div className="avatar-skill-chips">
              {selectedSkills.map((skill) => {
                const isPersonaDefault = personaSkillSet.has(skill);
                const removable = allowRemovePersonaSkills || !isPersonaDefault;
                return (
                  <span
                    key={skill}
                    className={`avatar-skill-chip${isPersonaDefault ? ' avatar-skill-chip--template' : ''}`}
                  >
                    <span className="avatar-skill-chip__name">{skill}</span>
                    {isPersonaDefault && (
                      <span className="avatar-skill-chip__badge">
                        {t('avatar.skills.templateDefault', '模板')}
                      </span>
                    )}
                    {removable && (
                      <button
                        type="button"
                        className="avatar-skill-chip__remove"
                        aria-label={t('avatar.skills.remove', '移除技能')}
                        onClick={() => handleRemoveSkill(skill)}
                      >
                        <X size={12} strokeWidth={2.5} />
                      </button>
                    )}
                  </span>
                );
              })}
            </div>
          ) : (
            <div className="avatar-skill-empty">
              <Wrench size={20} strokeWidth={1.75} className="avatar-skill-empty__icon" />
              <p>{t('avatar.skills.empty', '尚未绑定技能')}</p>
            </div>
          )}

          <div className="avatar-skill-actions">
            <div className="avatar-skill-add-select">
              <Plus size={15} className="avatar-skill-add-select__icon" aria-hidden />
              <select
                className="avatar-skill-add-select__control"
                value=""
                disabled={skillsLoading || addableSkills.length === 0}
                onChange={(e) => {
                  const value = e.target.value;
                  if (value) handleAddSkill(value);
                }}
              >
                <option value="">{addPlaceholder}</option>
                {addableSkills.map((skill) => (
                  <option key={skill.name} value={skill.name}>
                    {skill.name}
                    {skill.source ? ` (${skill.source})` : ''}
                  </option>
                ))}
              </select>
              <ChevronDown size={15} className="avatar-skill-add-select__chevron" aria-hidden />
            </div>

            <button
              type="button"
              className="avatar-platform__btn avatar-platform__btn--ghost avatar-skill-hub-btn"
              onClick={() => setHubOpen(true)}
              disabled={hubDisabled}
              title={hubDisabled ? t('avatar.skills.hubNeedSession', '请先连接会话后再从 Hub 安装') : undefined}
            >
              <Download size={15} strokeWidth={2} />
              {t('avatar.skills.browseHub', '从 Team Skills Hub 安装')}
            </button>
          </div>
        </div>
      </section>

      {hubOpen && sessionId && sessionId !== 'new' && (
        <TeamSkillsHubModal
          open={hubOpen}
          sessionId={sessionId}
          installedSkillNames={selectedSet}
          onClose={() => setHubOpen(false)}
          onInstalled={handleHubInstalled}
        />
      )}
    </>
  );
}
