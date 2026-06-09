import { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { SkillWhitelistTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { safeStringify } from '../../utils/format';
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

interface FormState {
  template_name: string;
  description: string;
  skill_id: string;
  skill_version: string;
  skill_source: string;
  data: string;
  enabled: boolean;
}

const empty: FormState = {
  template_name: '',
  description: '',
  skill_id: '',
  skill_version: '',
  skill_source: '',
  data: '',
  enabled: true,
};

export function SkillWhitelistTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const checkJson = useInvalidJsonChecker();
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (template) {
      setForm({
        template_name: template.template_name,
        description: template.description ?? '',
        skill_id: template.skill_id,
        skill_version: template.skill_version,
        skill_source: template.skill_source,
        data: safeStringify(template.data ?? {}, 2),
        enabled: template.enabled,
      });
    } else {
      setForm(empty);
    }
  }, [open, template]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    if (!form.template_name.trim()) {
      toast('warn', t('skillWhitelistTemplate.templateName'));
      return;
    }
    if (!form.skill_id.trim()) {
      toast('warn', t('skillWhitelistTemplate.skillId'));
      return;
    }
    if (!form.skill_version.trim()) {
      toast('warn', t('skillWhitelistTemplate.skillVersion'));
      return;
    }
    if (!form.skill_source.trim()) {
      toast('warn', t('skillWhitelistTemplate.skillSource'));
      return;
    }
    const dataErr = checkJson(form.data);
    if (dataErr) {
      toast('danger', dataErr);
      return;
    }

    const body: SkillWhitelistTemplateCreateBody | SkillWhitelistTemplateUpdateBody = {
      template_name: form.template_name.trim(),
      description: form.description.trim() || undefined,
      skill_id: form.skill_id.trim(),
      skill_version: form.skill_version.trim(),
      skill_source: form.skill_source.trim(),
      data: form.data.trim() ? (tryParseJson(form.data, {}) as Record<string, unknown>) : undefined,
      enabled: form.enabled,
    };

    setSaving(true);
    try {
      if (template) {
        await SkillWhitelistTemplateApi.update(template.template_id, body);
      } else {
        await SkillWhitelistTemplateApi.create(body as SkillWhitelistTemplateCreateBody);
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
          <label className="label">{t('skillWhitelistTemplate.templateName')}</label>
          <input
            className="input"
            value={form.template_name}
            onChange={(e) => update('template_name', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <label className="label">{t('common.detail')}</label>
          <input className="input" value={form.description} onChange={(e) => update('description', e.target.value)} />
        </div>
        <div>
          <label className="label">{t('skillWhitelistTemplate.skillId')}</label>
          <input
            className="input"
            placeholder={t('skillWhitelistTemplate.skillIdHint')}
            value={form.skill_id}
            onChange={(e) => update('skill_id', e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('skillWhitelistTemplate.skillVersion')}</label>
          <input
            className="input"
            placeholder="1.0.0"
            value={form.skill_version}
            onChange={(e) => update('skill_version', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <label className="label">{t('skillWhitelistTemplate.skillSource')}</label>
          <input
            className="input"
            placeholder="https://skillhub.example.com/"
            value={form.skill_source}
            onChange={(e) => update('skill_source', e.target.value)}
          />
        </div>
        <div className="md:col-span-2">
          <JsonField
            label={t('skillWhitelistTemplate.data')}
            value={form.data}
            onChange={(v) => update('data', v)}
            rows={5}
          />
        </div>
        <div className="md:col-span-2">
          <label className="flex items-center gap-2 cursor-pointer border border-border rounded-md px-3 py-2 w-fit hover:bg-bg-hover">
            <input
              type="checkbox"
              checked={form.enabled}
              onChange={(e) => update('enabled', e.target.checked)}
            />
            <span>{t('common.enabled')}</span>
          </label>
        </div>
      </div>
    </Modal>
  );
}
