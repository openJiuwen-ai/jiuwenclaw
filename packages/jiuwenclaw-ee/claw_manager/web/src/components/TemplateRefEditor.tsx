import { useCallback, useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ExtensionTemplateApi,
  ModelTemplateApi,
  ServiceConfigTemplateApi,
  SkillWhitelistTemplateApi,
} from '../services/api';
import type { ModelTemplate } from '../types';
import {
  TEMPLATE_REF_SLOTS,
  buildRefExpr,
  newTemplateRefRow,
  parseRefExpr,
  serializeTemplateRef,
  templateRefRowsFromMap,
  type ParsedRefExpr,
  type TemplateRefMap,
  type TemplateRefSlotRow,
} from '../utils/templateRef';

export interface TemplateOption {
  template_id: string;
  label: string;
}

function TrashIcon() {
  return (
    <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden>
      <path
        strokeLinecap="round"
        strokeLinejoin="round"
        d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
      />
    </svg>
  );
}

function DeleteIconButton({
  label,
  onClick,
  onAccent = false,
}: {
  label: string;
  onClick: () => void;
  onAccent?: boolean;
}) {
  return (
    <button
      type="button"
      className={
        onAccent
          ? 'btn sm ghost shrink-0 !border-transparent !px-2 !py-2 text-[var(--primary-foreground)] hover:!bg-black/10'
          : 'btn sm ghost shrink-0 !border-transparent !px-2 !py-2 text-muted hover:!bg-[var(--bg-hover)] hover:!text-[var(--text)]'
      }
      onClick={onClick}
      aria-label={label}
      title={label}
    >
      <TrashIcon />
    </button>
  );
}

interface TemplateRefEditorProps {
  label?: string;
  hint?: string;
  required?: boolean;
  value: TemplateRefMap;
  onChange: (value: TemplateRefMap) => void;
}

function modelMatchesSlot(template: ModelTemplate, slot: string): boolean {
  const types = template.model_type;
  if (slot === 'default_model') return types.includes('default');
  const kind = slot.replace(/_model$/, '');
  return types.includes(kind);
}

export async function loadTemplateOptions(): Promise<Record<string, TemplateOption[]>> {
  const pageSize = 200;
  const [models, skills, extensions, services] = await Promise.all([
    ModelTemplateApi.list({ page: 1, page_size: pageSize, enabled: true }),
    SkillWhitelistTemplateApi.list({ page: 1, page_size: pageSize, enabled: true }),
    ExtensionTemplateApi.list({ page: 1, page_size: pageSize, enabled: true }),
    ServiceConfigTemplateApi.list({ page: 1, page_size: pageSize, enabled: true }),
  ]);

  const modelItems = models.items ?? [];
  const toOpt = (id: string, name: string): TemplateOption => ({
    template_id: id,
    label: name ? `${name} (${id})` : id,
  });

  const modelSlots = ['default_model', 'video_model', 'audio_model', 'vision_model'] as const;
  const bySlot: Record<string, TemplateOption[]> = {};

  for (const slot of modelSlots) {
    const filtered = modelItems
      .filter((m) => modelMatchesSlot(m, slot))
      .map((m) => toOpt(m.template_id, m.template_name));
    bySlot[slot] = filtered.length ? filtered : modelItems.map((m) => toOpt(m.template_id, m.template_name));
  }

  bySlot.skill_whitelist = (skills.items ?? []).map((t) =>
    toOpt(t.template_id, t.template_name),
  );
  bySlot.extension_config = (extensions.items ?? []).map((t) =>
    toOpt(t.template_id, t.template_name),
  );
  bySlot.service_config = (services.items ?? []).map((t) =>
    toOpt(t.template_id, t.template_name),
  );

  return bySlot;
}

type RefMode = 'template' | 'user' | 'group';

function refModeFromExpr(expr: ParsedRefExpr): RefMode {
  if (expr.mode === 'user') return 'user';
  if (expr.mode === 'group') return 'group';
  return 'template';
}

function RefRowEditor({
  index,
  value,
  options,
  onChange,
  onRemove,
}: {
  index: number;
  value: string;
  options: TemplateOption[];
  onChange: (next: string) => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  const [expr, setExpr] = useState<ParsedRefExpr>(() => parseRefExpr(value));

  useEffect(() => {
    setExpr(parseRefExpr(value));
  }, [value]);

  const applyExpr = (next: ParsedRefExpr) => {
    setExpr(next);
    onChange(buildRefExpr(next));
  };

  const mode = refModeFromExpr(expr);

  const setMode = (nextMode: RefMode) => {
    applyExpr({
      mode: nextMode,
      templateId: '',
      custom: '',
      userId: '',
      groupId: '',
      fallbackId: '',
    });
  };

  return (
    <div className="flex items-center gap-2 rounded-md border border-[var(--border)] bg-[var(--card)] px-2.5 py-2 shadow-[inset_0_1px_0_var(--card-highlight)]">
      <span
        className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-[var(--bg-muted)] text-[11px] font-semibold text-muted tabular-nums"
        aria-hidden
      >
        {index}
      </span>
      <select
        className="select w-[8.5rem] shrink-0"
        value={mode}
        onChange={(e) => setMode(e.target.value as RefMode)}
      >
        <option value="template">{t('policies.templateRef.modeTemplate')}</option>
        <option value="user">{t('policies.templateRef.modeUser')}</option>
        <option value="group">{t('policies.templateRef.modeGroup')}</option>
      </select>

      <div className="flex-1 min-w-0">
        {mode === 'template' && (
          expr.mode === 'custom' ? (
            <input
              className="input mono text-xs w-full"
              value={expr.custom}
              placeholder={t('policies.templateRef.customPlaceholder')}
              onChange={(e) => applyExpr({ ...expr, mode: 'custom', custom: e.target.value })}
            />
          ) : (
            <select
              className="select w-full"
              value={expr.templateId}
              onChange={(e) => applyExpr({ ...expr, mode: 'template', templateId: e.target.value })}
            >
              <option value="">{t('policies.templateRef.pickTemplate')}</option>
              {options.map((opt) => (
                <option key={opt.template_id} value={opt.template_id}>
                  {opt.label}
                </option>
              ))}
            </select>
          )
        )}

        {mode === 'user' && (
          <input
            className="input w-full"
            value={expr.userId}
            placeholder={t('policies.templateRef.userIdPlaceholder')}
            onChange={(e) => applyExpr({ ...expr, mode: 'user', userId: e.target.value })}
          />
        )}

        {mode === 'group' && (
          <input
            className="input w-full"
            value={expr.groupId}
            placeholder={t('policies.templateRef.groupIdPlaceholder')}
            onChange={(e) => applyExpr({ ...expr, mode: 'group', groupId: e.target.value })}
          />
        )}
      </div>

      <DeleteIconButton
        label={t('policies.templateRef.removeRef')}
        onClick={onRemove}
      />
    </div>
  );
}

export function TemplateRefEditor({ label, hint, required, value, onChange }: TemplateRefEditorProps) {
  const { t } = useTranslation();
  const [rows, setRows] = useState<TemplateRefSlotRow[]>(() => templateRefRowsFromMap(value));
  const [templateOptions, setTemplateOptions] = useState<Record<string, TemplateOption[]>>({});
  const [loadingTemplates, setLoadingTemplates] = useState(false);

  const usedSlots = useMemo(() => new Set(rows.map((r) => r.slot)), [rows]);

  const emitChange = useCallback(
    (nextRows: TemplateRefSlotRow[]) => {
      setRows(nextRows);
      onChange(serializeTemplateRef(nextRows));
    },
    [onChange],
  );

  useEffect(() => {
    setRows((current) => {
      if (JSON.stringify(serializeTemplateRef(current)) === JSON.stringify(value)) {
        return current;
      }
      return templateRefRowsFromMap(value);
    });
  }, [value]);

  useEffect(() => {
    let cancelled = false;
    setLoadingTemplates(true);
    void loadTemplateOptions()
      .then((opts) => {
        if (!cancelled) setTemplateOptions(opts);
      })
      .finally(() => {
        if (!cancelled) setLoadingTemplates(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const addSlot = () => {
    const nextSlot =
      TEMPLATE_REF_SLOTS.find((s) => !usedSlots.has(s)) ?? TEMPLATE_REF_SLOTS[0];
    emitChange([...rows, newTemplateRefRow(nextSlot)]);
  };

  const updateSlot = (key: string, slot: string) => {
    emitChange(rows.map((r) => (r.key === key ? { ...r, slot } : r)));
  };

  const removeSlot = (key: string) => {
    emitChange(rows.filter((r) => r.key !== key));
  };

  const addRef = (key: string) => {
    emitChange(
      rows.map((r) => (r.key === key ? { ...r, refs: [...r.refs, ''] } : r)),
    );
  };

  const updateRef = (slotKey: string, index: number, refValue: string) => {
    emitChange(
      rows.map((r) => {
        if (r.key !== slotKey) return r;
        const refs = [...r.refs];
        refs[index] = refValue;
        return { ...r, refs };
      }),
    );
  };

  const removeRef = (slotKey: string, index: number) => {
    emitChange(
      rows.map((r) => {
        if (r.key !== slotKey) return r;
        const refs = r.refs.filter((_, i) => i !== index);
        return { ...r, refs: refs.length ? refs : [''] };
      }),
    );
  };

  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <label className="label !mb-0">
          {label ?? t('policies.templateRef.title')}
          {required ? <span className="text-danger ml-0.5" aria-hidden="true">*</span> : null}
        </label>
        {hint && <span className="text-[11px] text-muted">{hint}</span>}
      </div>
      {loadingTemplates && (
        <div className="text-[11px] text-muted mb-2">{t('policies.templateRef.loadingTemplates')}</div>
      )}

      <div className="flex flex-col gap-3">
        {rows.length === 0 ? (
          <div className="text-sm text-muted py-2">{t('policies.templateRef.empty')}</div>
        ) : (
          rows.map((row) => {
            const options = templateOptions[row.slot] ?? [];
            return (
              <div
                key={row.key}
                className="overflow-hidden rounded-lg border border-[var(--border)] shadow-sm"
              >
                <div className="flex items-center gap-3 border-b border-[var(--border)] bg-[var(--panel-strong)] px-3 py-2.5">
                  <span className="shrink-0 text-xs font-semibold tracking-wide text-muted">
                    {t('policies.templateRef.slot')}
                  </span>
                  <select
                    className="select min-w-0 flex-1"
                    value={row.slot}
                    onChange={(e) => updateSlot(row.key, e.target.value)}
                  >
                    {TEMPLATE_REF_SLOTS.map((slot) => (
                      <option
                        key={slot}
                        value={slot}
                        disabled={usedSlots.has(slot) && row.slot !== slot}
                      >
                        {t(`policies.templateRef.slots.${slot}`, { defaultValue: slot })}
                      </option>
                    ))}
                  </select>
                  <DeleteIconButton
                    label={t('policies.templateRef.removeSlot')}
                    onAccent
                    onClick={() => removeSlot(row.key)}
                  />
                </div>

                <div className="flex flex-col gap-2 bg-[var(--bg-muted)] px-3 py-3">
                  {row.refs.map((ref, index) => (
                    <RefRowEditor
                      key={`${row.key}-${index}`}
                      index={index + 1}
                      value={ref}
                      options={options}
                      onChange={(v) => updateRef(row.key, index, v)}
                      onRemove={() => removeRef(row.key, index)}
                    />
                  ))}
                  <button
                    type="button"
                    className="btn sm ghost mt-0.5 self-start border border-dashed border-[var(--border)]"
                    onClick={() => addRef(row.key)}
                  >
                    + {t('policies.templateRef.addRef')}
                  </button>
                </div>
              </div>
            );
          })
        )}

        <button type="button" className="btn sm primary self-start" onClick={addSlot}>
          + {t('policies.templateRef.addSlot')}
        </button>
      </div>
    </div>
  );
}
