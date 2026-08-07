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

export function AgentEditor({ draft, skillOptions, skillsStatus, saving, error, onChange, onReloadSkills, onCancel, onSave }: AgentEditorProps) {
  const { t } = useTranslation();
  const [touched, setTouched] = useState(false);
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
        </div>
      </header>

      <section className="agent-management-form-section">
        <h2>{t('agentManagement.form.basic')}</h2>
        <div className="agent-management-form-profile">
          <span className="agent-management-avatar-placeholder" aria-hidden="true">
            <img src="/agent-management/avatar-yellow.svg" alt="" />
            <span className="agent-management-avatar-placeholder__upload">
              <Upload size={14} aria-hidden="true" />
            </span>
          </span>
          <p>{t('agentManagement.form.avatarHint')}</p>
        </div>
        <div className="agent-management-form-grid">
          <label className="agent-management-form-field--wide">
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
              rows={4}
              maxLength={300}
              value={draft.description}
              onChange={event => update({ description: event.target.value })}
              placeholder={t('agentManagement.form.descriptionPlaceholder')}
              aria-invalid={Boolean(touched && errors.description)}
            />
            <small className="agent-management-field-count">{draft.description.length}/300</small>
            {touched && errors.description ? <small className="agent-management-field-error">{errors.description}</small> : null}
          </label>
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
