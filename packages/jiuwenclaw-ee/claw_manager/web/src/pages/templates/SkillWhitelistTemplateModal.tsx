import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { SkillWhitelistTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { findUnsafeTextField } from '../../utils/safeText';
import { isValidHttpUrl } from '../../utils/url';
import type {
  SkillWhitelistTemplate,
  SkillWhitelistTemplateCreateBody,
  SkillWhitelistTemplateUpdateBody,
} from '../../types';

interface Props {
  open: boolean;
  template: SkillWhitelistTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

/** 与 skill_whitelist_template 表 ColumnDefinition length 一致 */
const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
  skill_id: 512,
  skill_version: 64,
  skill_source: 2048,
} as const;

function clipField(value: string, max: number): string {
  return value.slice(0, max);
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

interface FormState {
  template_name: string;
  description: string;
  skill_id: string;
  skill_version: string;
  skill_source: string;
}

const empty: FormState = {
  template_name: '',
  description: '',
  skill_id: '',
  skill_version: '',
  skill_source: '',
};

export function SkillWhitelistTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (template) {
      setForm({
        template_name: clipField(template.template_name, FIELD_MAX_LENGTH.template_name),
        description: clipField(template.description ?? '', FIELD_MAX_LENGTH.description),
        skill_id: clipField(template.skill_id, FIELD_MAX_LENGTH.skill_id),
        skill_version: clipField(template.skill_version, FIELD_MAX_LENGTH.skill_version),
        skill_source: clipField(template.skill_source, FIELD_MAX_LENGTH.skill_source),
      });
    } else {
      setForm(empty);
    }
  }, [open, template]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    const requiredChecks: { label: string; invalid: boolean }[] = [
      { label: t('skillWhitelistTemplate.templateName'), invalid: !form.template_name.trim() },
      { label: t('skillWhitelistTemplate.skillSource'), invalid: !form.skill_source.trim() },
      { label: t('skillWhitelistTemplate.skillId'), invalid: !form.skill_id.trim() },
      { label: t('skillWhitelistTemplate.skillVersion'), invalid: !form.skill_version.trim() },
    ];
    const missing = requiredChecks.find((item) => item.invalid);
    if (missing) {
      toast('warn', t('skillWhitelistTemplate.fieldRequired', { field: missing.label }));
      return;
    }
    if (!isValidHttpUrl(form.skill_source)) {
      toast('warn', t('skillWhitelistTemplate.skillSourceInvalid'));
      return;
    }
    const unsafeField = findUnsafeTextField([
      { label: t('skillWhitelistTemplate.templateName'), value: form.template_name },
      { label: t('skillWhitelistTemplate.templateDescription'), value: form.description },
      { label: t('skillWhitelistTemplate.skillId'), value: form.skill_id },
      { label: t('skillWhitelistTemplate.skillVersion'), value: form.skill_version },
    ]);
    if (unsafeField) {
      toast('warn', t('skillWhitelistTemplate.unsafeText', { field: unsafeField }));
      return;
    }

    const body: SkillWhitelistTemplateCreateBody | SkillWhitelistTemplateUpdateBody = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      skill_id: form.skill_id.trim(),
      skill_version: form.skill_version.trim(),
      skill_source: form.skill_source.trim(),
    };

    setSaving(true);
    try {
      if (template) {
        await SkillWhitelistTemplateApi.update(template.template_id, body);
      } else {
        await SkillWhitelistTemplateApi.create({ ...body, enabled: true } as SkillWhitelistTemplateCreateBody);
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={template ? t('skillWhitelistTemplate.edit') : t('skillWhitelistTemplate.new')}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn primary" onClick={submit} disabled={saving}>
            {saving ? t('common.loading') : t('common.save')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div className="md:col-span-2">
          <FieldLabel required>{t('skillWhitelistTemplate.templateName')}</FieldLabel>
          <LimitedTextInput
            value={form.template_name}
            maxLength={FIELD_MAX_LENGTH.template_name}
            onChange={(v) => update('template_name', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('skillWhitelistTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput
            value={form.description}
            maxLength={FIELD_MAX_LENGTH.description}
            onChange={(v) => update('description', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel required>{t('skillWhitelistTemplate.skillSource')}</FieldLabel>
          <LimitedTextInput
            value={form.skill_source}
            maxLength={FIELD_MAX_LENGTH.skill_source}
            onChange={(v) => update('skill_source', v)}
          />
        </div>
        <div>
          <FieldLabel required>{t('skillWhitelistTemplate.skillId')}</FieldLabel>
          <LimitedTextInput
            value={form.skill_id}
            maxLength={FIELD_MAX_LENGTH.skill_id}
            onChange={(v) => update('skill_id', v)}
          />
        </div>
        <div>
          <FieldLabel required>{t('skillWhitelistTemplate.skillVersion')}</FieldLabel>
          <LimitedTextInput
            value={form.skill_version}
            maxLength={FIELD_MAX_LENGTH.skill_version}
            onChange={(v) => update('skill_version', v)}
          />
        </div>
      </div>
    </Modal>
  );
}
