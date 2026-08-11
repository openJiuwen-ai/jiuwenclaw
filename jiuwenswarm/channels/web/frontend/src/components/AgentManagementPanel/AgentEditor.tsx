import { ArrowLeft, Check, Plus, Upload } from 'lucide-react';
import { useMemo, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import type { AgentDraft, RequestStatus, SkillOption } from '../../features/agentManagement';

type AgentEditorProps = {
  draft: AgentDraft;
  skillOptions: SkillOption[];
  skillsStatus: RequestStatus;
  saving: boolean;
  error: string | null;
  onChange: (draft: AgentDraft) => void;
  onReloadSkills: () => void;
  onCancel: () => void;
  onSave: () => void;
};

const TAG_OPTIONS = [
  { id: 'code-delivery', labelKey: 'agentManagement.form.tagOptions.codeDelivery' },
  { id: 'code-review', labelKey: 'agentManagement.form.tagOptions.codeReview' },
  { id: 'bug-fix', labelKey: 'agentManagement.form.tagOptions.bugFix' },
] as const;

export function AgentEditor({ draft, skillOptions, skillsStatus, saving, error, onChange, onReloadSkills, onCancel, onSave }: AgentEditorProps) {
  const { t } = useTranslation();
  const [touched, setTouched] = useState(false);
  const [tagMenuOpen, setTagMenuOpen] = useState(false);
  const errors = useMemo(
    () => ({
      id: !draft.id.trim()
        ? t('agentManagement.form.errors.idRequired')
        : !/^[a-zA-Z0-9][a-zA-Z0-9._-]{2,49}$/.test(draft.id)
          ? t('agentManagement.form.errors.idInvalid')
          : '',
      name: !draft.name.trim() ? t('agentManagement.form.errors.nameRequired') : '',
      description: !draft.description.trim() ? t('agentManagement.form.errors.descriptionRequired') : '',
      persona: !draft.persona.trim() ? t('agentManagement.form.errors.personaRequired') : '',
    }),
    [draft, t],
  );
  const hasErrors = Object.values(errors).some(Boolean);

  const update = (patch: Partial<AgentDraft>) => onChange({ ...draft, ...patch });

  const toggleSkill = (skillId: string) => {
    const skillRefs = draft.skillRefs.includes(skillId) ? draft.skillRefs.filter(item => item !== skillId) : [...draft.skillRefs, skillId];
    update({ skillRefs });
  };

  const toggleTag = (tagId: string) => {
    const tagIds = draft.tagIds.includes(tagId) ? draft.tagIds.filter(item => item !== tagId) : [...draft.tagIds, tagId];
    update({ tagIds });
  };

  const updatePrompt = (index: number, value: string) => {
    const suggestedPrompts = draft.suggestedPrompts.map((prompt, promptIndex) => (promptIndex === index ? value : prompt));
    update({ suggestedPrompts });
  };

  const addPrompt = () => update({ suggestedPrompts: [...draft.suggestedPrompts, ''] });

  const removePrompt = (index: number) => update({ suggestedPrompts: draft.suggestedPrompts.filter((_, promptIndex) => promptIndex !== index) });

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    setTouched(true);
    if (!hasErrors) onSave();
  };

  return (
    <form className="agent-management-editor" onSubmit={handleSubmit} data-testid="agent-editor">
      <button type="button" className="agent-management-back" onClick={onCancel}>
        <ArrowLeft size={16} aria-hidden="true" />
        {t('agentManagement.actions.back')}
      </button>

      <header className="agent-management-editor__header">
        <h1>{t('agentManagement.form.title')}</h1>
        <div className="agent-management-editor__tabs" role="tablist" aria-label={t('agentManagement.form.createTabsLabel')}>
          <span className="is-active" role="tab" aria-selected="true">
            {t('agentManagement.form.createAgentTab')}
          </span>
          <span className="is-disabled" role="tab" aria-selected="false" aria-disabled="true">
            {t('agentManagement.form.createTeamTab')}
          </span>
          <span className="is-disabled" role="tab" aria-selected="false" aria-disabled="true">
            {t('agentManagement.tabs.mine')}
          </span>
        </div>
      </header>

      <section className="agent-management-form-section">
        <h2>{t('agentManagement.form.basic')}</h2>
        <div className="agent-management-form-profile">
          <span className="agent-management-avatar-placeholder" aria-hidden="true">
            <img src="/agent-management/avatar-yellow.svg" alt="" />
          </span>
          <span className="agent-management-avatar-placeholder__upload" aria-hidden="true">
            <Upload size={14} aria-hidden="true" />
          </span>
          <p>{t('agentManagement.form.avatarHint')}</p>
        </div>
        <div className="agent-management-form-grid">
          <label className="agent-management-form-field--wide agent-management-form-field--runtime-id">
            <span>{t('agentManagement.form.idLabel')}</span>
            <input
              value={draft.id}
              onChange={event => update({ id: event.target.value })}
              placeholder={t('agentManagement.form.idPlaceholder')}
              aria-invalid={Boolean(touched && errors.id)}
            />
            {touched && errors.id ? <small className="agent-management-field-error">{errors.id}</small> : null}
          </label>
          <label className="agent-management-form-field--wide">
            <span>{t('agentManagement.form.nameLabel')}</span>
            <input
              value={draft.name}
              onChange={event => update({ name: event.target.value })}
              placeholder={t('agentManagement.form.namePlaceholder')}
              aria-invalid={Boolean(touched && errors.name)}
            />
            {touched && errors.name ? <small className="agent-management-field-error">{errors.name}</small> : null}
          </label>
          <label className="agent-management-form-field--wide">
            <span>{t('agentManagement.form.descriptionLabel')}</span>
            <textarea
              rows={2}
              maxLength={226}
              value={draft.description}
              onChange={event => update({ description: event.target.value })}
              placeholder={t('agentManagement.form.descriptionPlaceholder')}
              aria-invalid={Boolean(touched && errors.description)}
            />
            <small className="agent-management-field-count">{draft.description.length}/226</small>
            {touched && errors.description ? <small className="agent-management-field-error">{errors.description}</small> : null}
          </label>
          <div className="agent-management-form-field--wide agent-management-form-field--tag-picker">
            <span>{t('agentManagement.form.tagLabel')}</span>
            <div className="agent-management-tag-picker">
              <button
                type="button"
                className="agent-management-tag-picker__trigger"
                aria-expanded={tagMenuOpen}
                aria-haspopup="listbox"
                onClick={() => setTagMenuOpen(open => !open)}
              >
                <span className="agent-management-tag-picker__values">
                  {draft.tagIds.length > 0 ? (
                    draft.tagIds.map(tagId => {
                      const option = TAG_OPTIONS.find(item => item.id === tagId);
                      return option ? (
                        <span key={tagId} className="agent-management-tag agent-management-tag--selected">
                          {t(option.labelKey)}
                          <span aria-hidden="true">×</span>
                        </span>
                      ) : null;
                    })
                  ) : (
                    <span className="agent-management-form-placeholder">{t('agentManagement.form.tagPlaceholder')}</span>
                  )}
                </span>
                <span className="agent-management-tag-picker__chevron" aria-hidden="true">
                  ⌄
                </span>
              </button>
              {tagMenuOpen ? (
                <div className="agent-management-tag-picker__options" role="listbox" aria-label={t('agentManagement.form.tagLabel')}>
                  {TAG_OPTIONS.map(option => {
                    const selected = draft.tagIds.includes(option.id);
                    return (
                      <button
                        key={option.id}
                        type="button"
                        role="option"
                        aria-selected={selected}
                        className={selected ? 'is-selected' : ''}
                        onClick={() => toggleTag(option.id)}
                      >
                        {t(option.labelKey)}
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          </div>
          <label className="agent-management-form-field--wide">
            <span>{t('agentManagement.form.personaLabel')}</span>
            <textarea
              className="agent-management-persona"
              rows={12}
              value={draft.persona}
              onChange={event => update({ persona: event.target.value })}
              placeholder={t('agentManagement.form.personaPlaceholder')}
              aria-invalid={Boolean(touched && errors.persona)}
            />
            {touched && errors.persona ? <small className="agent-management-field-error">{errors.persona}</small> : null}
          </label>
        </div>
      </section>

      <section className="agent-management-form-section agent-management-form-section--skills">
        <div className="agent-management-form-section__header">
          <div>
            <h2>{t('agentManagement.form.skillsLabel')}</h2>
            <p>{t('agentManagement.form.skillsHint')}</p>
          </div>
          <button type="button" className="agent-management-inline-action" onClick={onReloadSkills}>
            <Plus size={14} aria-hidden="true" />
            {t('agentManagement.form.refreshSkills')}
          </button>
        </div>
        {skillsStatus === 'loading' ? <p className="agent-management-form-muted">{t('common.loading')}</p> : null}
        {skillsStatus === 'error' ? (
          <div className="agent-management-form-error">
            <span>{t('agentManagement.form.skillsError')}</span>
            <button type="button" onClick={onReloadSkills}>
              {t('common.retry')}
            </button>
          </div>
        ) : null}
        {skillsStatus === 'success' && skillOptions.length === 0 ? (
          <p className="agent-management-form-muted">{t('agentManagement.form.skillsEmpty')}</p>
        ) : null}
        {skillsStatus === 'success' ? (
          <div className="agent-management-skill-options">
            {skillOptions.map(skill => {
              const checked = draft.skillRefs.includes(skill.id);
              return (
                <button
                  key={skill.id}
                  type="button"
                  className={`agent-management-skill-option${checked ? ' is-selected' : ''}`}
                  onClick={() => toggleSkill(skill.id)}
                  aria-pressed={checked}
                >
                  <span className="agent-management-skill-option__icon">
                    {checked ? <Check size={14} aria-hidden="true" /> : <Plus size={14} aria-hidden="true" />}
                  </span>
                  <span>
                    <strong>{skill.name}</strong>
                    <small>{skill.description}</small>
                  </span>
                </button>
              );
            })}
          </div>
        ) : null}
      </section>

      <section className="agent-management-form-section agent-management-form-section--prompts">
        <div className="agent-management-form-section__header">
          <div>
            <h2>{t('agentManagement.form.promptsLabel')}</h2>
          </div>
          <button type="button" className="agent-management-inline-action" onClick={addPrompt}>
            <Plus size={14} aria-hidden="true" />
            {t('agentManagement.form.addPrompt')}
          </button>
        </div>
        <div className="agent-management-prompt-editor-list">
          {draft.suggestedPrompts.map((prompt, index) => (
            <div className="agent-management-prompt-editor" key={index}>
              <input value={prompt} onChange={event => updatePrompt(index, event.target.value)} placeholder={t('agentManagement.form.promptPlaceholder')} />
              <button type="button" onClick={() => removePrompt(index)} aria-label={t('agentManagement.form.removePrompt')}>
                ×
              </button>
            </div>
          ))}
        </div>
      </section>

      {error ? (
        <div className="agent-management-form-error agent-management-form-error--submit" role="alert">
          {error}
        </div>
      ) : null}
      <footer className="agent-management-editor__footer">
        <button type="button" className="agent-management-button agent-management-button--secondary" onClick={onCancel} disabled={saving}>
          {t('common.cancel')}
        </button>
        <button type="submit" className="agent-management-button agent-management-button--primary" disabled={saving}>
          {saving ? t('common.saving') : t('common.confirm')}
        </button>
      </footer>
    </form>
  );
}
