import { ArrowLeft, Check, ChevronDown, ChevronUp, Minus, Plus, Search, Trash2, X } from 'lucide-react';
import { createPortal } from 'react-dom';
import { useEffect, useMemo, useRef, useState, type FormEvent } from 'react';
import { useTranslation } from 'react-i18next';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { AgentDraft, McpOption, RequestStatus, SkillOption } from '../../features/agentManagement';

type AgentEditorProps = {
  draft: AgentDraft;
  skillOptions: SkillOption[];
  skillsStatus: RequestStatus;
  mcpOptions: McpOption[];
  mcpStatus: RequestStatus;
  saving: boolean;
  error: string | null;
  onChange: (draft: AgentDraft) => void;
  onReloadSkills: () => void;
  onCancel: () => void;
  onSave: () => void;
};

const TAG_OPTIONS = [
  { id: 'product-development', labelKey: 'agentManagement.categories.ProductDevelopment' },
  { id: 'marketing', labelKey: 'agentManagement.categories.Marketing' },
  { id: 'efficiency', labelKey: 'agentManagement.categories.Efficiency' },
  { id: 'data-analysis', labelKey: 'agentManagement.categories.DataAnalysis' },
  { id: 'content-creation', labelKey: 'agentManagement.categories.ContentCreation' },
  { id: 'safety-compliance', labelKey: 'agentManagement.categories.SafetyCompliance' },
  { id: 'communication', labelKey: 'agentManagement.categories.Communication' },
] as const;

const MCP_TYPE_OPTIONS = [
  ['stdio-mcp', 'connectorMarket.detail.integrationType.stdioMcp'],
  ['remote-mcp', 'connectorMarket.detail.integrationType.remoteMcp'],
  ['cli', 'connectorMarket.detail.integrationType.cli'],
  ['skill-only', 'connectorMarket.detail.integrationType.skillOnly'],
] as const;

export function AgentEditor({
  draft,
  skillOptions,
  skillsStatus,
  mcpOptions,
  mcpStatus,
  saving,
  error,
  onChange,
  onReloadSkills,
  onCancel,
  onSave,
}: AgentEditorProps) {
  const { t } = useTranslation();
  const [touched, setTouched] = useState(false);
  const [tagMenuOpen, setTagMenuOpen] = useState(false);
  const [mcpOpen, setMcpOpen] = useState(true);
  const [skillsOpen, setSkillsOpen] = useState(true);
  const [promptsOpen, setPromptsOpen] = useState(true);
  const [personaEditing, setPersonaEditing] = useState(false);
  const [skillDialogOpen, setSkillDialogOpen] = useState(false);
  const [mcpDialogOpen, setMcpDialogOpen] = useState(false);
  const [skillQuery, setSkillQuery] = useState('');
  const [mcpQuery, setMcpQuery] = useState('');
  const [mcpType, setMcpType] = useState('');
  const [mcpTypeOpen, setMcpTypeOpen] = useState(false);
  const [mcpTab, setMcpTab] = useState<'mine' | 'market'>('market');
  const [skillDraft, setSkillDraft] = useState<string[]>(draft.skillRefs);
  const [mcpDraft, setMcpDraft] = useState<string[]>(draft.mcpRefs);
  const tagPickerRef = useRef<HTMLDivElement>(null);
  const personaSurfaceRef = useRef<HTMLDivElement>(null);
  const mcpTypeRef = useRef<HTMLDivElement>(null);

  const errors = useMemo(
    () => ({
      name: !draft.name.trim() ? t('agentManagement.form.errors.nameRequired') : '',
      description: !draft.description.trim() ? t('agentManagement.form.errors.descriptionRequired') : '',
      persona: !draft.persona.trim() ? t('agentManagement.form.errors.personaRequired') : '',
    }),
    [draft.description, draft.name, draft.persona, t],
  );
  const hasErrors = Object.values(errors).some(Boolean);
  const selectedSkills = skillOptions.filter(skill => draft.skillRefs.includes(skill.id));
  const selectedMcps = mcpOptions.filter(mcp => draft.mcpRefs.includes(mcp.id));
  const filteredSkills = skillOptions.filter(skill => `${skill.name} ${skill.description}`.toLocaleLowerCase().includes(skillQuery.trim().toLocaleLowerCase()));
  const filteredMcps = mcpOptions.filter(mcp => {
    const matchesTab = mcpTab === 'mine' ? mcp.source === 'customize' : mcp.source === 'built_in';
    const matchesQuery = `${mcp.name} ${mcp.description}`.toLocaleLowerCase().includes(mcpQuery.trim().toLocaleLowerCase());
    const matchesType = !mcpType || mcp.integrationType === mcpType;
    return matchesTab && matchesQuery && matchesType;
  });
  const selectedMcpType = MCP_TYPE_OPTIONS.find(([value]) => value === mcpType);

  useEffect(() => {
    if (!tagMenuOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!tagPickerRef.current?.contains(event.target as Node)) setTagMenuOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [tagMenuOpen]);

  useEffect(() => {
    if (!personaEditing) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!personaSurfaceRef.current?.contains(event.target as Node)) setPersonaEditing(false);
    };
    document.addEventListener('pointerdown', handlePointerDown, true);
    return () => document.removeEventListener('pointerdown', handlePointerDown, true);
  }, [personaEditing]);

  useEffect(() => {
    if (!mcpTypeOpen) return;
    const handlePointerDown = (event: PointerEvent) => {
      if (!mcpTypeRef.current?.contains(event.target as Node)) setMcpTypeOpen(false);
    };
    document.addEventListener('pointerdown', handlePointerDown);
    return () => document.removeEventListener('pointerdown', handlePointerDown);
  }, [mcpTypeOpen]);

  const update = (patch: Partial<AgentDraft>) => onChange({ ...draft, ...patch });

  const toggleTag = (tagId: string) => {
    const tagIds = draft.tagIds.includes(tagId) ? draft.tagIds.filter(item => item !== tagId) : [...draft.tagIds, tagId];
    update({ tagIds });
  };

  const openSkillDialog = () => {
    setSkillDraft(draft.skillRefs);
    setSkillQuery('');
    setSkillDialogOpen(true);
  };

  const openMcpDialog = () => {
    setMcpDraft(draft.mcpRefs);
    setMcpQuery('');
    setMcpType('');
    setMcpTypeOpen(false);
    setMcpTab('market');
    setMcpDialogOpen(true);
  };

  const updatePrompt = (index: number, value: string) => {
    const suggestedPrompts = draft.suggestedPrompts.map((prompt, promptIndex) => (promptIndex === index ? value : prompt));
    update({ suggestedPrompts });
  };

  const addPrompt = () => {
    if (draft.suggestedPrompts.some(prompt => prompt.trim().length === 0)) return;
    update({ suggestedPrompts: [...draft.suggestedPrompts, ''] });
  };

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
        </div>
      </header>

      <section className="agent-management-form-section">
        <h2>{t('agentManagement.form.basic')}</h2>
        <div className="agent-management-form-grid">
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
          <div className="agent-management-form-field--wide agent-management-form-field--tag-picker" ref={tagPickerRef}>
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
                        <button
                          key={tagId}
                          type="button"
                          className="agent-management-tag agent-management-tag--selected"
                          aria-label={t('agentManagement.form.removeTag', { name: t(option.labelKey) })}
                          onClick={(event) => {
                            event.stopPropagation();
                            toggleTag(tagId);
                          }}
                        >
                          {t(option.labelKey)}
                          <span aria-hidden="true">×</span>
                        </button>
                      ) : null;
                    })
                  ) : (
                    <span className="agent-management-form-placeholder">{t('agentManagement.form.tagPlaceholder')}</span>
                  )}
                </span>
                <span className="agent-management-tag-picker__chevron" aria-hidden="true">⌄</span>
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
                        <span>{t(option.labelKey)}</span>
                        {selected ? <Check size={14} aria-hidden="true" /> : null}
                      </button>
                    );
                  })}
                </div>
              ) : null}
            </div>
          </div>
          <div className="agent-management-form-field--wide agent-management-persona-field">
            <span>{t('agentManagement.form.personaLabel')}</span>
            <div className="agent-management-persona-surface" ref={personaSurfaceRef}>
              {personaEditing ? (
              <textarea
                className="agent-management-persona"
                rows={12}
                value={draft.persona}
                onChange={event => update({ persona: event.target.value })}
                placeholder={t('agentManagement.form.personaPlaceholder')}
                aria-invalid={Boolean(touched && errors.persona)}
                aria-label={t('agentManagement.form.personaLabel')}
                onBlur={() => setPersonaEditing(false)}
              />
              ) : (
                <div
                  className="agent-management-persona-rendered"
                  role="button"
                  tabIndex={0}
                  aria-label={t('agentManagement.form.personaPreview')}
                  onClick={() => setPersonaEditing(true)}
                  onKeyDown={event => {
                    if (event.key === 'Enter' || event.key === ' ') {
                      event.preventDefault();
                      setPersonaEditing(true);
                    }
                  }}
                >
                  {draft.persona.trim() ? <div className="agent-management-markdown"><ReactMarkdown remarkPlugins={[remarkGfm]}>{draft.persona}</ReactMarkdown></div> : <span className="agent-management-persona-placeholder">{t('agentManagement.form.personaPlaceholder')}</span>}
                </div>
              )}
            </div>
            {touched && errors.persona ? <small className="agent-management-field-error">{errors.persona}</small> : null}
          </div>
        </div>
      </section>

      <section className="agent-management-form-section agent-management-form-section--mcp">
        <div className="agent-management-form-section__header">
          <div className="agent-management-form-section__heading">
            <button type="button" className="agent-management-section-toggle" aria-expanded={mcpOpen} aria-label={t('agentManagement.form.mcpToggle')} onClick={() => setMcpOpen(open => !open)}>
              {mcpOpen ? <ChevronUp size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
            </button>
            <div><h2>{t('agentManagement.form.mcpLabel')}</h2></div>
          </div>
          <button type="button" className="agent-management-inline-action" onClick={openMcpDialog}><Plus size={14} aria-hidden="true" />{t('agentManagement.form.addMcp')}</button>
        </div>
        {mcpOpen ? (
          selectedMcps.length > 0 ? (
            <div className="agent-management-selected-capabilities">
              {selectedMcps.map(mcp => (
                <article className="agent-management-capability-card" key={mcp.id}>
                  <span className="agent-management-capability-card__icon">{mcp.name.slice(0, 1).toUpperCase()}</span>
                  <span><strong>{mcp.name}</strong><small>{mcp.description}</small></span>
                  <button type="button" className="agent-management-capability-card__remove" aria-label={t('agentManagement.form.removeMcp', { name: mcp.name })} onClick={() => update({ mcpRefs: draft.mcpRefs.filter(id => id !== mcp.id) })}>
                    <Trash2 size={16} aria-hidden="true" />
                  </button>
                </article>
              ))}
            </div>
          ) : null
        ) : null}
      </section>

      <section className="agent-management-form-section agent-management-form-section--skills">
        <div className="agent-management-form-section__header">
          <div className="agent-management-form-section__heading">
            <button type="button" className="agent-management-section-toggle" aria-expanded={skillsOpen} aria-label={t('agentManagement.form.skillsToggle')} onClick={() => setSkillsOpen(open => !open)}>
              {skillsOpen ? <ChevronUp size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
            </button>
            <div><h2>{t('agentManagement.form.skillsLabel')}</h2></div>
          </div>
          <button type="button" className="agent-management-inline-action" onClick={openSkillDialog}><Plus size={14} aria-hidden="true" />{t('agentManagement.form.addSkill')}</button>
        </div>
        {skillsOpen ? (
          <>
            {skillsStatus === 'loading' ? <p className="agent-management-form-muted">{t('common.loading')}</p> : null}
            {skillsStatus === 'error' ? <div className="agent-management-form-error"><span>{t('agentManagement.form.skillsError')}</span><button type="button" onClick={onReloadSkills}>{t('common.retry')}</button></div> : null}
            {selectedSkills.length > 0 ? (
              <div className="agent-management-selected-capabilities">
                {selectedSkills.map(skill => (
                  <article className="agent-management-capability-card" key={skill.id}>
                    <span className="agent-management-capability-card__icon">{skill.name.slice(0, 1).toUpperCase()}</span>
                    <span><strong>{skill.name}</strong><small>{skill.description}</small></span>
                    <button type="button" className="agent-management-capability-card__remove" aria-label={t('agentManagement.form.removeSkill', { name: skill.name })} onClick={() => update({ skillRefs: draft.skillRefs.filter(id => id !== skill.id) })}><Trash2 size={16} aria-hidden="true" /></button>
                  </article>
                ))}
              </div>
            ) : null}
          </>
        ) : null}
      </section>

      <section className="agent-management-form-section agent-management-form-section--prompts">
        <div className="agent-management-form-section__header">
          <div className="agent-management-form-section__heading">
            <button type="button" className="agent-management-section-toggle" aria-expanded={promptsOpen} aria-label={t('agentManagement.form.promptsToggle')} onClick={() => setPromptsOpen(open => !open)}>
              {promptsOpen ? <ChevronUp size={18} aria-hidden="true" /> : <ChevronDown size={18} aria-hidden="true" />}
            </button>
            <h2>{t('agentManagement.form.promptsLabel')}</h2>
          </div>
          <button type="button" className="agent-management-inline-action" onClick={addPrompt}><Plus size={14} aria-hidden="true" />{t('agentManagement.form.addPrompt')}</button>
        </div>
        {promptsOpen ? (
          draft.suggestedPrompts.length > 0 ? (
            <div className="agent-management-prompt-editor-list">
              {draft.suggestedPrompts.map((prompt, index) => (
                <div className="agent-management-prompt-editor" key={index}>
                  <input value={prompt} onChange={event => updatePrompt(index, event.target.value)} placeholder={t('agentManagement.form.promptPlaceholder')} />
                  <button type="button" onClick={() => removePrompt(index)} aria-label={t('agentManagement.form.removePrompt')}><Minus size={16} aria-hidden="true" /></button>
                </div>
              ))}
            </div>
          ) : null
        ) : null}
      </section>

      {error ? <div className="agent-management-form-error agent-management-form-error--submit" role="alert">{error}</div> : null}
      <footer className="agent-management-editor__footer">
        <button type="button" className="agent-management-button agent-management-button--secondary" onClick={onCancel} disabled={saving}>{t('common.cancel')}</button>
        <button type="submit" className="agent-management-button agent-management-button--primary" disabled={saving}>{saving ? t('common.saving') : t('common.confirm')}</button>
      </footer>

      {skillDialogOpen ? createPortal(
        <div className="agent-management-selection-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setSkillDialogOpen(false); }}>
          <section className="agent-management-selection-dialog" role="dialog" aria-modal="true" aria-labelledby="agent-skill-dialog-title">
            <header><h2 id="agent-skill-dialog-title">{t('agentManagement.form.selectSkill')}</h2><button type="button" onClick={() => setSkillDialogOpen(false)} aria-label={t('common.cancel')}><X size={16} aria-hidden="true" /></button></header>
            <div className="agent-management-selection-tabs" role="tablist" aria-label={t('agentManagement.form.selectSkill')}>
              <button type="button" className="agent-management-selection-tab is-active" role="tab" aria-selected="true">{t('agentManagement.form.mySkills')}</button>
              <span className="agent-management-selection-tab" role="tab" aria-selected="false" aria-disabled="true">{t('agentManagement.form.skillMarket')}</span>
            </div>
            <label className="agent-management-selection-search">
              <Search size={16} aria-hidden="true" />
              <input type="search" value={skillQuery} onChange={event => setSkillQuery(event.target.value)} placeholder={t('agentManagement.form.selectionSearchPlaceholder')} />
            </label>
            <div className={`agent-management-selection-dialog__body${skillsStatus === 'success' && filteredSkills.length === 0 ? ' is-empty' : ''}`}>
              {skillsStatus === 'loading' ? <p className="agent-management-form-muted">{t('common.loading')}</p> : null}
              {skillsStatus === 'error' ? <div className="agent-management-form-error"><span>{t('agentManagement.form.skillsError')}</span><button type="button" onClick={onReloadSkills}>{t('common.retry')}</button></div> : null}
              {skillsStatus === 'success' && filteredSkills.length === 0 ? <div className="agent-management-selection-empty-state"><p>{t('agentManagement.form.skillsEmpty')}</p></div> : null}
              {skillsStatus === 'success' && filteredSkills.length > 0 ? <div className="agent-management-selection-grid">{filteredSkills.map(skill => {
                const selected = skillDraft.includes(skill.id);
                return <button key={skill.id} type="button" className={`agent-management-selection-card${selected ? ' is-selected' : ''}`} onClick={() => setSkillDraft(current => selected ? current.filter(id => id !== skill.id) : [...current, skill.id])} aria-pressed={selected}><span className="agent-management-capability-card__icon">{skill.name.slice(0, 1).toUpperCase()}</span><span><strong>{skill.name}</strong><small>{skill.description}</small></span><span className="agent-management-selection-card__action" aria-hidden="true">{selected ? <Minus size={16} /> : <Plus size={16} />}</span></button>;
              })}</div> : null}
            </div>
            <footer><span>{t('agentManagement.form.selectedCount', { count: skillDraft.length })}</span><div><button type="button" className="agent-management-button agent-management-button--secondary" onClick={() => setSkillDialogOpen(false)}>{t('common.cancel')}</button><button type="button" className="agent-management-button agent-management-button--primary" onClick={() => { update({ skillRefs: skillDraft }); setSkillDialogOpen(false); }}>{t('common.confirm')}</button></div></footer>
          </section>
        </div>,
        document.body,
      ) : null}

      {mcpDialogOpen ? createPortal(
        <div className="agent-management-selection-overlay" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setMcpDialogOpen(false); }}>
          <section className="agent-management-selection-dialog" role="dialog" aria-modal="true" aria-labelledby="agent-mcp-dialog-title">
            <header><h2 id="agent-mcp-dialog-title">{t('agentManagement.form.selectMcp')}</h2><button type="button" onClick={() => setMcpDialogOpen(false)} aria-label={t('common.cancel')}><X size={16} aria-hidden="true" /></button></header>
            <div className="agent-management-selection-tabs" role="tablist" aria-label={t('agentManagement.form.selectMcp')}>
              <button type="button" className={`agent-management-selection-tab${mcpTab === 'mine' ? ' is-active' : ''}`} role="tab" aria-selected={mcpTab === 'mine'} onClick={() => setMcpTab('mine')}>{t('agentManagement.form.myMcp')}</button>
              <button type="button" className={`agent-management-selection-tab${mcpTab === 'market' ? ' is-active' : ''}`} role="tab" aria-selected={mcpTab === 'market'} onClick={() => setMcpTab('market')}>{t('agentManagement.form.mcpMarket')}</button>
            </div>
            <div className="agent-management-selection-controls">
              <div className="agent-management-selection-filter" ref={mcpTypeRef}>
                <button type="button" className="agent-management-selection-filter__trigger" aria-haspopup="listbox" aria-expanded={mcpTypeOpen} onClick={() => setMcpTypeOpen(open => !open)}>
                  <span>{selectedMcpType ? t(selectedMcpType[1]) : t('agentManagement.form.mcpTypeAll')}</span>
                  <ChevronDown size={14} aria-hidden="true" />
                </button>
                {mcpTypeOpen ? <div className="agent-management-selection-filter__menu" role="listbox" aria-label={t('agentManagement.form.mcpTypeFilter')}>
                  <button type="button" role="option" aria-selected={!mcpType} onClick={() => { setMcpType(''); setMcpTypeOpen(false); }}>{t('agentManagement.form.mcpTypeAll')}{!mcpType ? <Check size={14} aria-hidden="true" /> : null}</button>
                  {MCP_TYPE_OPTIONS.map(([value, labelKey]) => <button type="button" role="option" aria-selected={mcpType === value} key={value} onClick={() => { setMcpType(value); setMcpTypeOpen(false); }}>{t(labelKey)}{mcpType === value ? <Check size={14} aria-hidden="true" /> : null}</button>)}
                </div> : null}
              </div>
              <label className="agent-management-selection-search">
                <Search size={16} aria-hidden="true" />
                <input type="search" value={mcpQuery} onChange={event => setMcpQuery(event.target.value)} placeholder={t('agentManagement.form.selectionSearchPlaceholder')} />
              </label>
            </div>
            <div className={`agent-management-selection-dialog__body${mcpStatus === 'success' && filteredMcps.length === 0 ? ' is-empty' : ''}`}>
              {mcpStatus === 'loading' ? <p className="agent-management-form-muted">{t('common.loading')}</p> : null}
              {mcpStatus === 'error' ? <p className="agent-management-form-muted">{t('agentManagement.form.mcpError')}</p> : null}
              {mcpStatus === 'success' && filteredMcps.length === 0 ? <div className="agent-management-selection-empty-state"><p>{t('agentManagement.form.mcpEmpty')}</p></div> : null}
              {mcpStatus === 'success' && filteredMcps.length > 0 ? <div className="agent-management-selection-grid">{filteredMcps.map(mcp => {
                const selected = mcpDraft.includes(mcp.id);
                return <button key={mcp.id} type="button" className={`agent-management-selection-card${selected ? ' is-selected' : ''}`} onClick={() => setMcpDraft(current => selected ? current.filter(id => id !== mcp.id) : [...current, mcp.id])} aria-pressed={selected}><span className="agent-management-capability-card__icon">{mcp.name.slice(0, 1).toUpperCase()}</span><span><strong>{mcp.name}</strong><small>{mcp.description}</small></span><span className="agent-management-selection-card__action" aria-hidden="true">{selected ? <Minus size={16} /> : <Plus size={16} />}</span></button>;
              })}</div> : null}
            </div>
            <footer><span>{t('agentManagement.form.selectedCount', { count: mcpDraft.length })}</span><div><button type="button" className="agent-management-button agent-management-button--secondary" onClick={() => setMcpDialogOpen(false)}>{t('common.cancel')}</button><button type="button" className="agent-management-button agent-management-button--primary" onClick={() => { update({ mcpRefs: mcpDraft }); setMcpDialogOpen(false); }}>{t('common.confirm')}</button></div></footer>
          </section>
        </div>,
        document.body,
      ) : null}
    </form>
  );
}
