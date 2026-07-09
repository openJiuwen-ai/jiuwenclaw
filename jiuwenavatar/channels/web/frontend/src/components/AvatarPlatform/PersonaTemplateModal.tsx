import { ChangeEvent, useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { AvatarFormModal } from './AvatarFormModal';
import { PersonaIcon } from './PersonaIcon';
import { TeamSkillsHubModal } from '../../features/TeamSkillsHubModal';
import type { CodingEngine, PersonaConfig } from '../../stores/avatarStore';
import type { WebRequestOptions } from '../../types';

const CODING_ENGINES: CodingEngine[] = ['jiuwen-coding', 'claude-code', 'codex'];
const MAX_ICON_SIZE = 5 * 1024 * 1024;

function joinList(value: string[]): string {
  return value.join(', ');
}

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

function makeDefaultPersona(): PersonaConfig {
  return {
    id: '',
    display_name: '',
    description: '',
    icon: 'avatar',
    version: '1.0.0',
    coding_capable: false,
    coding_engines: [],
    default_coding_engine: null,
    skills: [],
    trigger_templates: [],
    system_prompt: '',
    report_template: { title: '执行报告', sections: [] },
    tags: ['自定义'],
    builtin: false,
  };
}

interface PersonaTemplateModalProps {
  mode: 'create' | 'edit';
  persona?: PersonaConfig;
  sessionId: string;
  sendRequest: (method: string, params?: Record<string, unknown>, options?: WebRequestOptions) => Promise<unknown>;
  onGenerate?: (prompt: string) => Promise<PersonaConfig | null>;
  onClose: () => void;
  onSave: (persona: PersonaConfig) => Promise<void>;
}

async function cropIconFile(file: File): Promise<string> {
  if (!/^image\/(png|jpeg|webp)$/.test(file.type)) {
    throw new Error('仅支持 PNG / JPG / WebP 图片');
  }
  if (file.size > MAX_ICON_SIZE) {
    throw new Error('图片不能超过 5MB');
  }
  const dataUrl = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ''));
    reader.onerror = () => reject(new Error('图片读取失败'));
    reader.readAsDataURL(file);
  });
  const img = await new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve(image);
    image.onerror = () => reject(new Error('图片解析失败'));
    image.src = dataUrl;
  });
  const size = Math.min(img.width, img.height);
  const sx = (img.width - size) / 2;
  const sy = (img.height - size) / 2;
  const canvas = document.createElement('canvas');
  canvas.width = 256;
  canvas.height = 256;
  const ctx = canvas.getContext('2d');
  if (!ctx) throw new Error('当前浏览器不支持图片裁切');
  ctx.drawImage(img, sx, sy, size, size, 0, 0, 256, 256);
  return canvas.toDataURL('image/png');
}

export function PersonaTemplateModal({
  mode,
  persona,
  sessionId,
  sendRequest,
  onGenerate,
  onClose,
  onSave,
}: PersonaTemplateModalProps) {
  const { t } = useTranslation();
  const initial = useMemo(() => ({ ...makeDefaultPersona(), ...(persona || {}) }), [persona]);
  const [form, setForm] = useState<PersonaConfig>(initial);
  const [selectedSkills, setSelectedSkills] = useState<string[]>(initial.skills);
  const [tagsText, setTagsText] = useState(joinList(initial.tags));
  const [installedSkills, setInstalledSkills] = useState<SkillListItem[]>([]);
  const [skillQuery, setSkillQuery] = useState('');
  const [hubOpen, setHubOpen] = useState(false);
  const [generatePrompt, setGeneratePrompt] = useState('');
  const [generating, setGenerating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const update = <K extends keyof PersonaConfig>(key: K, value: PersonaConfig[K]) => {
    setForm((prev) => ({ ...prev, [key]: value }));
  };

  const toggleEngine = (engine: CodingEngine) => {
    const engines = (form.coding_engines || []) as CodingEngine[];
    const next = engines.includes(engine)
      ? engines.filter((item) => item !== engine)
      : [...engines, engine];
    update('coding_engines', next);
    if (!next.includes(form.default_coding_engine as CodingEngine)) {
      update('default_coding_engine', next[0] || null);
    }
  };

  const fetchInstalledSkills = useCallback(async () => {
    if (!sessionId || sessionId === 'new') return;
    const data = await sendRequest('skills.list', {
      session_id: sessionId,
      with_installed: true,
    }) as { skills?: SkillListItem[]; plugins?: InstalledPluginItem[] };
    const byName = new Map<string, SkillListItem>();
    for (const skill of data.skills || []) {
      if (skill.name) byName.set(skill.name, skill);
    }
    for (const plugin of data.plugins || []) {
      if (plugin.enabled === false) continue;
      const source = plugin.marketplace || 'installed';
      for (const name of plugin.skills || []) {
        if (name && !byName.has(name)) byName.set(name, { name, source });
      }
      if (plugin.plugin_name && !byName.has(plugin.plugin_name)) {
        byName.set(plugin.plugin_name, { name: plugin.plugin_name, source });
      }
    }
    setInstalledSkills([...byName.values()].sort((a, b) => a.name.localeCompare(b.name)));
  }, [sendRequest, sessionId]);

  useEffect(() => {
    void fetchInstalledSkills().catch(() => setInstalledSkills([]));
  }, [fetchInstalledSkills]);

  const selectedSet = useMemo(() => new Set(selectedSkills), [selectedSkills]);
  const filteredSkills = useMemo(() => {
    const q = skillQuery.trim().toLowerCase();
    return installedSkills
      .filter((skill) => !selectedSet.has(skill.name))
      .filter((skill) => !q || skill.name.toLowerCase().includes(q) || skill.description?.toLowerCase().includes(q))
      .slice(0, 12);
  }, [installedSkills, selectedSet, skillQuery]);

  const addSkill = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed || selectedSet.has(trimmed)) return;
    setSelectedSkills((prev) => [...prev, trimmed]);
    setSkillQuery('');
  };

  const removeSkill = (name: string) => {
    setSelectedSkills((prev) => prev.filter((item) => item !== name));
  };

  const handleIconChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = '';
    if (!file) return;
    try {
      const icon = await cropIconFile(file);
      update('icon', icon);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const handleGenerate = async () => {
    if (generating || !onGenerate || !generatePrompt.trim()) return;
    setGenerating(true);
    setError(null);
    try {
      const draft = await onGenerate(generatePrompt.trim());
      if (!draft) throw new Error(t('avatar.personaTemplate.generateEmpty', '未生成有效模板'));
      setForm((prev) => ({ ...prev, ...draft, icon: prev.icon?.startsWith('data:image/') ? prev.icon : draft.icon || 'avatar' }));
      setSelectedSkills(draft.skills || []);
      setTagsText(joinList(draft.tags || []));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setGenerating(false);
    }
  };

  const handleSave = async () => {
    if (generating) return;
    setSaving(true);
    setError(null);
    try {
      const tags = tagsText
        .split(/[\n,，]/)
        .map((item) => item.trim())
        .filter(Boolean);
      const codingEngines = form.coding_capable ? ((form.coding_engines || []) as CodingEngine[]) : [];
      const payload: PersonaConfig = {
        ...form,
        id: form.id.trim(),
        display_name: form.display_name.trim(),
        description: form.description.trim(),
        icon: form.icon.trim() || 'avatar',
        version: form.version.trim() || '1.0.0',
        builtin: false,
        skills: selectedSkills,
        tags,
        coding_engines: codingEngines,
        default_coding_engine: form.coding_capable ? form.default_coding_engine || codingEngines[0] || null : null,
        trigger_templates: form.trigger_templates || [],
        report_template: form.report_template || { title: '执行报告', sections: [] },
      };
      await onSave(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <AvatarFormModal
      title={mode === 'create' ? t('avatar.personaTemplate.createTitle', '新建 Persona 模板') : t('avatar.personaTemplate.editTitle', '编辑 Persona 模板')}
      subtitle={t('avatar.personaTemplate.subtitle', '配置一个可复用的分身模板，用于后续创建分身。')}
      personaIcon={form.icon || 'avatar'}
      onClose={onClose}
      disableClose={generating}
      error={error}
      footer={
        <>
          <button type="button" className="avatar-platform__btn avatar-platform__btn--ghost" disabled={generating} onClick={onClose}>
            {t('common.cancel', '取消')}
          </button>
          <button type="button" className="avatar-platform__btn avatar-platform__btn--primary" disabled={saving || generating} onClick={handleSave}>
            {saving ? t('common.saving', '保存中...') : t('common.save', '保存')}
          </button>
        </>
      }
    >
      <div className={`persona-template-form-shell${generating ? ' persona-template-form-shell--busy' : ''}`}>
        {generating && (
          <div className="persona-template-generate-overlay" role="status" aria-live="polite">
            <div className="persona-template-generate-overlay__panel">
              <div className="persona-template-generate-overlay__title">
                {t('avatar.personaTemplate.generateBusyTitle', 'AI 正在生成 Persona 模板')}
              </div>
              <div className="persona-template-generate-overlay__desc">
                {t('avatar.personaTemplate.generateBusyDesc', '正在生成系统提示词并匹配推荐技能，请稍候，期间暂不可编辑表单。')}
              </div>
              <div className="persona-template-generate-progress">
                <span />
              </div>
            </div>
          </div>
        )}
        <fieldset className="persona-template-form-fieldset" disabled={generating}>
      {mode === 'create' && onGenerate && (
        <section className="avatar-form-section persona-template-ai">
          <div className="avatar-form-section__head">
            <div>
              <h4 className="avatar-form-section__title">{t('avatar.personaTemplate.aiTitle', '一句话生成模板')}</h4>
              <p className="avatar-form-section__hint">
                {t('avatar.personaTemplate.aiHint', '描述你想要的角色，系统会自动补全名称、提示词和推荐技能。')}
              </p>
            </div>
          </div>
          <div className="avatar-form-section__body persona-template-ai__body">
            <input
              className="avatar-platform__input"
              value={generatePrompt}
              placeholder={t('avatar.personaTemplate.aiPlaceholder', '例如：我想创建一个老中医的 persona 模板')}
              onChange={(e) => setGeneratePrompt(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void handleGenerate();
              }}
            />
            <button
              type="button"
              className="avatar-platform__btn avatar-platform__btn--primary"
              disabled={generating || !generatePrompt.trim()}
              onClick={() => void handleGenerate()}
            >
              {generating ? t('avatar.personaTemplate.generating', '生成中...') : t('avatar.personaTemplate.generate', '生成模板')}
            </button>
          </div>
        </section>
      )}

      <section className="avatar-form-section">
        <div className="avatar-form-section__head">
          <div>
            <h4 className="avatar-form-section__title">{t('avatar.personaTemplate.basic', '基础信息')}</h4>
            <p className="avatar-form-section__hint">{t('avatar.personaTemplate.basicHint', 'ID 保存后不可修改，用于文件名和 API 引用。')}</p>
          </div>
        </div>
        <div className="avatar-form-section__body persona-template-form__grid">
          <label className="persona-template-form__field">
            <span>{t('avatar.personaTemplate.id', '模板 ID')}</span>
            <input
              className="avatar-platform__input"
              value={form.id}
              disabled={mode === 'edit'}
              placeholder="my-reviewer"
              onChange={(e) => update('id', e.target.value)}
            />
          </label>
          <label className="persona-template-form__field">
            <span>{t('avatar.personaTemplate.name', '模板名称')}</span>
            <input
              className="avatar-platform__input"
              value={form.display_name}
              placeholder="我的代码审阅分身"
              onChange={(e) => update('display_name', e.target.value)}
            />
          </label>
          <label className="persona-template-form__field">
            <span>{t('avatar.personaTemplate.version', '版本')}</span>
            <input
              className="avatar-platform__input"
              value={form.version}
              placeholder="1.0.0"
              onChange={(e) => update('version', e.target.value)}
            />
          </label>
          <div className="persona-template-form__field">
            <span>{t('avatar.personaTemplate.icon', '图标')}</span>
            <div className="persona-template-icon-upload">
              <PersonaIcon icon={form.icon || 'avatar'} />
              <label className="avatar-platform__btn avatar-platform__btn--ghost persona-template-icon-upload__btn">
                {t('avatar.personaTemplate.uploadIcon', '上传图标')}
                <input
                  type="file"
                  accept="image/png,image/jpeg,image/webp"
                  onChange={(event) => void handleIconChange(event)}
                />
              </label>
              {form.icon?.startsWith('data:image/') && (
                <button type="button" className="avatar-platform__btn avatar-platform__btn--ghost" onClick={() => update('icon', 'avatar')}>
                  {t('avatar.personaTemplate.resetIcon', '重置')}
                </button>
              )}
            </div>
            <span className="persona-template-form__help">
              {t('avatar.personaTemplate.iconHint', '支持 PNG/JPG/WebP，自动居中裁成正方形。')}
            </span>
          </div>
          <label className="persona-template-form__field persona-template-form__field--full">
            <span>{t('avatar.personaTemplate.description', '描述')}</span>
            <textarea
              className="avatar-platform__textarea persona-template-form__textarea--sm"
              value={form.description}
              onChange={(e) => update('description', e.target.value)}
            />
          </label>
        </div>
      </section>

      <section className="avatar-form-section">
        <div className="avatar-form-section__head">
          <div>
            <h4 className="avatar-form-section__title">{t('avatar.personaTemplate.coding', '编码能力')}</h4>
            <p className="avatar-form-section__hint">{t('avatar.personaTemplate.codingHint', '需要执行代码任务时启用，并选择可用编码后端。')}</p>
          </div>
        </div>
        <div className="avatar-form-section__body persona-template-form__stack">
          <label className="persona-template-form__check">
            <input
              type="checkbox"
              checked={!!form.coding_capable}
              onChange={(e) => update('coding_capable', e.target.checked)}
            />
            <span>{t('avatar.personaTemplate.enableCoding', '启用编码能力')}</span>
          </label>
          {form.coding_capable && (
            <>
              <div className="persona-template-form__checks">
                {CODING_ENGINES.map((engine) => (
                  <label key={engine} className="persona-template-form__check">
                    <input
                      type="checkbox"
                      checked={((form.coding_engines || []) as CodingEngine[]).includes(engine)}
                      onChange={() => toggleEngine(engine)}
                    />
                    <span>{engine}</span>
                  </label>
                ))}
              </div>
              <label className="persona-template-form__field">
                <span>{t('avatar.personaTemplate.defaultEngine', '默认编码后端')}</span>
                <select
                  className="avatar-platform__select"
                  value={form.default_coding_engine || ''}
                  onChange={(e) => update('default_coding_engine', (e.target.value || null) as CodingEngine | null)}
                >
                  {((form.coding_engines || []) as CodingEngine[]).map((engine) => (
                    <option key={engine} value={engine}>{engine}</option>
                  ))}
                </select>
              </label>
            </>
          )}
        </div>
      </section>

      <section className="avatar-form-section">
        <div className="avatar-form-section__head">
          <div>
            <h4 className="avatar-form-section__title">{t('avatar.personaTemplate.skillsTags', '技能与标签')}</h4>
            <p className="avatar-form-section__hint">{t('avatar.personaTemplate.skillHint', '从已安装技能中搜索选择，也可以从 Team Skills Hub 安装后自动加入。')}</p>
          </div>
        </div>
        <div className="avatar-form-section__body persona-template-form__grid">
          <div className="persona-template-form__field persona-template-form__field--full">
            <span>{t('common.skills', '技能')}</span>
            {selectedSkills.length > 0 ? (
              <div className="avatar-skill-chips">
                {selectedSkills.map((skill) => (
                  <span key={skill} className="avatar-skill-chip">
                    <span className="avatar-skill-chip__name">{skill}</span>
                    <button type="button" className="avatar-skill-chip__remove" onClick={() => removeSkill(skill)}>
                      ×
                    </button>
                  </span>
                ))}
              </div>
            ) : (
              <div className="avatar-skill-empty">
                <p>{t('avatar.skills.empty', '尚未绑定技能')}</p>
              </div>
            )}
            <div className="persona-template-skill-picker">
              <input
                className="avatar-platform__input"
                value={skillQuery}
                placeholder={t('avatar.personaTemplate.searchInstalledSkills', '搜索已安装技能...')}
                onChange={(e) => setSkillQuery(e.target.value)}
              />
              <button type="button" className="avatar-platform__btn avatar-platform__btn--ghost" onClick={() => setHubOpen(true)} disabled={!sessionId || sessionId === 'new'}>
                {t('avatar.skills.browseHub', '从 Team Skills Hub 安装')}
              </button>
            </div>
            {filteredSkills.length > 0 && (
              <div className="persona-template-skill-results">
                {filteredSkills.map((skill) => (
                  <button
                    key={skill.name}
                    type="button"
                    className="persona-template-skill-result"
                    onClick={() => addSkill(skill.name)}
                  >
                    <span>{skill.name}</span>
                    {skill.source && <small>{skill.source}</small>}
                  </button>
                ))}
              </div>
            )}
          </div>
          <label className="persona-template-form__field persona-template-form__field--full">
            <span>{t('avatar.personaTemplate.tags', '标签')}</span>
            <input
              className="avatar-platform__input"
              value={tagsText}
              placeholder="自定义, 代码审阅"
              onChange={(e) => setTagsText(e.target.value)}
            />
          </label>
        </div>
      </section>

      <section className="avatar-form-section">
        <div className="avatar-form-section__head">
          <div>
            <h4 className="avatar-form-section__title">{t('avatar.systemPrompt.label', '系统提示')}</h4>
            <p className="avatar-form-section__hint">{t('avatar.personaTemplate.promptHint', '定义该模板创建出的分身默认行为。')}</p>
          </div>
        </div>
        <div className="avatar-form-section__body">
          <textarea
            className="avatar-platform__textarea avatar-form-textarea"
            rows={7}
            value={form.system_prompt}
            onChange={(e) => update('system_prompt', e.target.value)}
          />
        </div>
      </section>
        </fieldset>
      </div>
      {hubOpen && sessionId && sessionId !== 'new' && (
        <TeamSkillsHubModal
          open={hubOpen}
          sessionId={sessionId}
          installedSkillNames={selectedSet}
          onClose={() => setHubOpen(false)}
          onInstalled={async (skillName) => {
            addSkill(skillName);
            await fetchInstalledSkills();
          }}
        />
      )}
    </AvatarFormModal>
  );
}
