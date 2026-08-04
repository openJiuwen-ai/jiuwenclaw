import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { LimitedTextInput } from '../../components/LimitedTextInput';
import { ServiceConfigTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { isValidK8sCpu, isValidK8sMemory } from '../../utils/k8sResource';
import { isValidOptionalUnixAbsPath, isValidUnixAbsPath } from '../../utils/path';
import { findUnsafeTextField } from '../../utils/safeText';
import type {
  ServiceConfigTemplate,
  ServiceConfigTemplateCreateBody,
  ServiceConfigTemplateUpdateBody,
} from '../../types';

interface Props {
  open: boolean;
  template: ServiceConfigTemplate | null;
  onClose: () => void;
  onSaved: () => void;
}

interface FormState {
  template_name: string;
  description: string;
  agent_image: string;
  pod_name: string;
  container_name: string;
  container_port: number;
  port_name: string;
  image_pull_policy: string;
  replicas: number;
  kubeconfig: string;
  agent_runtime: string;
  readiness_initial_delay: number;
  readiness_period: number;
  ready_timeout: number;
  ready_poll_interval: number;
  nfs_server: string;
  nfs_path: string;
  nfs_mount_path: string;
  agent_cpu_request: string;
  agent_memory_request: string;
  agent_cpu_limit: string;
  agent_memory_limit: string;
  jiuwenbox_cpu_request: string;
  jiuwenbox_memory_request: string;
  jiuwenbox_cpu_limit: string;
  jiuwenbox_memory_limit: string;
  min_idle_services: number;
  max_services: number;
  service_concurrency: number;
  service_ttl: number;
  autoscale_interval: number;
  message_timeout: number;
  session_concurrency: number;
  session_ttl: number;
}

const FIELD_MAX_LENGTH = {
  template_name: 128,
  description: 512,
  agent_image: 512,
  pod_name: 128,
  container_name: 128,
  port_name: 64,
  kubeconfig: 512,
  nfs_server: 256,
  nfs_path: 512,
  nfs_mount_path: 512,
  agent_cpu_request: 32,
  agent_memory_request: 32,
  agent_cpu_limit: 32,
  agent_memory_limit: 32,
  jiuwenbox_cpu_request: 32,
  jiuwenbox_memory_request: 32,
  jiuwenbox_cpu_limit: 32,
  jiuwenbox_memory_limit: 32,
} as const;

/** 与后端 / 库表类型上限一致 */
const INT32_MAX = 2_147_483_647;
const DECIMAL_10_3_MAX = 9_999_999.999;

const NUM_LIMITS = {
  container_port: { min: 1, max: 65535, labelKey: 'containerPort' },
  replicas: { min: 1, max: INT32_MAX, labelKey: 'replicas' },
  readiness_initial_delay: { min: 0, max: INT32_MAX, labelKey: 'readinessInitialDelay' },
  readiness_period: { min: 1, max: INT32_MAX, labelKey: 'readinessPeriod' },
  ready_timeout: { min: 1, max: INT32_MAX, labelKey: 'readyTimeout' },
  ready_poll_interval: { min: 1, max: INT32_MAX, labelKey: 'readyPollInterval' },
  min_idle_services: { min: 0, max: INT32_MAX, labelKey: 'minIdleServices' },
  max_services: { min: 1, max: INT32_MAX, labelKey: 'maxServices' },
  service_concurrency: { min: 1, max: INT32_MAX, labelKey: 'serviceConcurrency' },
  service_ttl: { min: 1, max: INT32_MAX, labelKey: 'serviceTtl' },
  autoscale_interval: { min: 0.001, max: DECIMAL_10_3_MAX, labelKey: 'autoscaleInterval' },
  message_timeout: { min: 1, max: INT32_MAX, labelKey: 'messageTimeout' },
  session_concurrency: { min: 1, max: INT32_MAX, labelKey: 'sessionConcurrency' },
  session_ttl: { min: 1, max: INT32_MAX, labelKey: 'sessionTtl' },
} as const;

const empty: FormState = {
  template_name: '',
  description: '',
  agent_image: '',
  pod_name: '',
  container_name: 'agentserver',
  container_port: 8080,
  port_name: 'http',
  image_pull_policy: 'IfNotPresent',
  replicas: 1,
  kubeconfig: '',
  agent_runtime: '',
  readiness_initial_delay: 10,
  readiness_period: 5,
  ready_timeout: 300,
  ready_poll_interval: 5,
  nfs_server: '',
  nfs_path: '/',
  nfs_mount_path: '',
  agent_cpu_request: '',
  agent_memory_request: '',
  agent_cpu_limit: '',
  agent_memory_limit: '',
  jiuwenbox_cpu_request: '',
  jiuwenbox_memory_request: '',
  jiuwenbox_cpu_limit: '',
  jiuwenbox_memory_limit: '',
  min_idle_services: 1,
  max_services: 20,
  service_concurrency: 10,
  service_ttl: 180,
  autoscale_interval: 5,
  message_timeout: 60,
  session_concurrency: 10,
  session_ttl: 60,
};

function opt(v: string) {
  return v.trim() || undefined;
}

function FieldLabel({ children, required }: { children: ReactNode; required?: boolean }) {
  return (
    <label className="label">
      {children}
      {required && <span className="text-danger ml-0.5" aria-hidden="true">*</span>}
    </label>
  );
}

function SectionTitle({ children }: { children: ReactNode }) {
  return (
    <div className="md:col-span-2 text-[11px] font-semibold uppercase tracking-wide text-muted pt-2 border-t border-border">
      {children}
    </div>
  );
}

export function ServiceConfigTemplateModal({ open, template, onClose, onSaved }: Props) {
  const { t } = useTranslation();
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (template) {
      setForm({
        template_name: template.template_name,
        description: template.description ?? '',
        agent_image: template.agent_image,
        pod_name: template.pod_name ?? '',
        container_name: template.container_name,
        container_port: template.container_port,
        port_name: template.port_name,
        image_pull_policy: template.image_pull_policy,
        replicas: template.replicas,
        kubeconfig: template.kubeconfig ?? '',
        agent_runtime: template.agent_runtime ?? '',
        readiness_initial_delay: template.readiness_initial_delay,
        readiness_period: template.readiness_period,
        ready_timeout: template.ready_timeout,
        ready_poll_interval: template.ready_poll_interval,
        nfs_server: template.nfs_server ?? '',
        nfs_path: template.nfs_path,
        nfs_mount_path: template.nfs_mount_path ?? '',
        agent_cpu_request: template.agent_cpu_request ?? '',
        agent_memory_request: template.agent_memory_request ?? '',
        agent_cpu_limit: template.agent_cpu_limit ?? '',
        agent_memory_limit: template.agent_memory_limit ?? '',
        jiuwenbox_cpu_request: template.jiuwenbox_cpu_request ?? '',
        jiuwenbox_memory_request: template.jiuwenbox_memory_request ?? '',
        jiuwenbox_cpu_limit: template.jiuwenbox_cpu_limit ?? '',
        jiuwenbox_memory_limit: template.jiuwenbox_memory_limit ?? '',
        min_idle_services: template.min_idle_services,
        max_services: template.max_services,
        service_concurrency: template.service_concurrency,
        service_ttl: template.service_ttl,
        autoscale_interval: template.autoscale_interval,
        message_timeout: template.message_timeout,
        session_concurrency: template.session_concurrency,
        session_ttl: template.session_ttl,
      });
    } else {
      setForm(empty);
    }
  }, [open, template]);

  const update = <K extends keyof FormState>(k: K, v: FormState[K]) =>
    setForm((s) => ({ ...s, [k]: v }));

  const submit = async () => {
    const required: { ok: boolean; label: string }[] = [
      { ok: !!form.template_name.trim(), label: t('serviceConfigTemplate.templateName') },
      { ok: !!form.agent_image.trim(), label: t('serviceConfigTemplate.agentImage') },
      { ok: !!form.container_name.trim(), label: t('serviceConfigTemplate.containerName') },
    ];
    const missing = required.find((r) => !r.ok);
    if (missing) {
      toast('warn', t('serviceConfigTemplate.fieldRequired', { field: missing.label }));
      return;
    }
    for (const [key, limit] of Object.entries(NUM_LIMITS)) {
      const value = form[key as keyof typeof NUM_LIMITS];
      if (!Number.isFinite(value) || value < limit.min || value > limit.max) {
        toast(
          'warn',
          t('serviceConfigTemplate.numberOutOfRange', {
            field: t(`serviceConfigTemplate.${limit.labelKey}`),
            min: limit.min,
            max: limit.max,
          }),
        );
        return;
      }
    }
    if (form.min_idle_services > form.max_services) {
      toast('warn', t('serviceConfigTemplate.poolRangeInvalid'));
      return;
    }
    const cpuFields = [
      ['agent_cpu_request', 'agentCpuRequest'],
      ['agent_cpu_limit', 'agentCpuLimit'],
      ['jiuwenbox_cpu_request', 'jiuwenboxCpuRequest'],
      ['jiuwenbox_cpu_limit', 'jiuwenboxCpuLimit'],
    ] as const;
    for (const [key, labelKey] of cpuFields) {
      if (!isValidK8sCpu(form[key])) {
        toast(
          'warn',
          t('serviceConfigTemplate.cpuQuantityInvalid', {
            field: t(`serviceConfigTemplate.${labelKey}`),
          }),
        );
        return;
      }
    }
    const memoryFields = [
      ['agent_memory_request', 'agentMemoryRequest'],
      ['agent_memory_limit', 'agentMemoryLimit'],
      ['jiuwenbox_memory_request', 'jiuwenboxMemoryRequest'],
      ['jiuwenbox_memory_limit', 'jiuwenboxMemoryLimit'],
    ] as const;
    for (const [key, labelKey] of memoryFields) {
      if (!isValidK8sMemory(form[key])) {
        toast(
          'warn',
          t('serviceConfigTemplate.memoryQuantityInvalid', {
            field: t(`serviceConfigTemplate.${labelKey}`),
          }),
        );
        return;
      }
    }
    const nfsPath = form.nfs_path.trim();
    if (nfsPath && !isValidUnixAbsPath(nfsPath)) {
      toast(
        'warn',
        t('serviceConfigTemplate.pathInvalid', {
          field: t('serviceConfigTemplate.nfsPath'),
        }),
      );
      return;
    }
    if (!isValidOptionalUnixAbsPath(form.nfs_mount_path)) {
      toast(
        'warn',
        t('serviceConfigTemplate.pathInvalid', {
          field: t('serviceConfigTemplate.nfsMountPath'),
        }),
      );
      return;
    }
    const unsafeField = findUnsafeTextField([
      { label: t('serviceConfigTemplate.templateName'), value: form.template_name },
      { label: t('serviceConfigTemplate.templateDescription'), value: form.description },
      { label: t('serviceConfigTemplate.agentImage'), value: form.agent_image },
      { label: t('serviceConfigTemplate.podName'), value: form.pod_name },
      { label: t('serviceConfigTemplate.containerName'), value: form.container_name },
      { label: t('serviceConfigTemplate.portName'), value: form.port_name },
      { label: t('serviceConfigTemplate.agentRuntime'), value: form.agent_runtime },
    ]);
    if (unsafeField) {
      toast('warn', t('serviceConfigTemplate.unsafeText', { field: unsafeField }));
      return;
    }

    const body: ServiceConfigTemplateCreateBody | ServiceConfigTemplateUpdateBody = {
      template_name: form.template_name.trim(),
      description: opt(form.description),
      agent_image: form.agent_image.trim(),
      pod_name: opt(form.pod_name),
      container_name: form.container_name.trim(),
      container_port: form.container_port,
      port_name: form.port_name.trim() || undefined,
      image_pull_policy: form.image_pull_policy.trim() || undefined,
      replicas: form.replicas,
      kubeconfig: opt(form.kubeconfig),
      agent_runtime: opt(form.agent_runtime),
      readiness_initial_delay: form.readiness_initial_delay,
      readiness_period: form.readiness_period,
      ready_timeout: form.ready_timeout,
      ready_poll_interval: form.ready_poll_interval,
      nfs_server: opt(form.nfs_server),
      nfs_path: form.nfs_path.trim() || undefined,
      nfs_mount_path: opt(form.nfs_mount_path),
      agent_cpu_request: opt(form.agent_cpu_request),
      agent_memory_request: opt(form.agent_memory_request),
      agent_cpu_limit: opt(form.agent_cpu_limit),
      agent_memory_limit: opt(form.agent_memory_limit),
      jiuwenbox_cpu_request: opt(form.jiuwenbox_cpu_request),
      jiuwenbox_memory_request: opt(form.jiuwenbox_memory_request),
      jiuwenbox_cpu_limit: opt(form.jiuwenbox_cpu_limit),
      jiuwenbox_memory_limit: opt(form.jiuwenbox_memory_limit),
      min_idle_services: form.min_idle_services,
      max_services: form.max_services,
      service_concurrency: form.service_concurrency,
      service_ttl: form.service_ttl,
      autoscale_interval: form.autoscale_interval,
      message_timeout: form.message_timeout,
      session_concurrency: form.session_concurrency,
      session_ttl: form.session_ttl,
    };

    setSaving(true);
    try {
      if (template) {
        await ServiceConfigTemplateApi.update(template.template_id, body);
      } else {
        await ServiceConfigTemplateApi.create({ ...body, enabled: true } as ServiceConfigTemplateCreateBody);
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  const numField = (
    key: keyof typeof NUM_LIMITS,
    label: string,
    step?: number,
  ) => {
    const limit = NUM_LIMITS[key];
    return (
      <div>
        <label className="label">{label}</label>
        <input
          className="input"
          type="number"
          min={limit.min}
          max={limit.max}
          step={step}
          value={form[key] as number}
          onChange={(e) => update(key, Number(e.target.value) as FormState[typeof key])}
        />
      </div>
    );
  };

  const textField = (
    key: keyof FormState,
    label: string,
    options: { maxLength: number; required?: boolean; placeholder?: string },
  ) => (
    <div>
      <FieldLabel required={options.required}>{label}</FieldLabel>
      {options.placeholder ? (
        <input
          className="input"
          placeholder={options.placeholder}
          value={form[key] as string}
          maxLength={options.maxLength}
          onChange={(e) => update(key, e.target.value.slice(0, options.maxLength) as FormState[typeof key])}
        />
      ) : (
        <LimitedTextInput
          value={form[key] as string}
          maxLength={options.maxLength}
          onChange={(v) => update(key, v as FormState[typeof key])}
        />
      )}
    </div>
  );

  return (
    <Modal
      open={open}
      title={template ? t('serviceConfigTemplate.edit') : t('serviceConfigTemplate.new')}
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
          <FieldLabel required>{t('serviceConfigTemplate.templateName')}</FieldLabel>
          <LimitedTextInput
            value={form.template_name}
            maxLength={FIELD_MAX_LENGTH.template_name}
            onChange={(v) => update('template_name', v)}
          />
        </div>
        <div className="md:col-span-2">
          <FieldLabel>{t('serviceConfigTemplate.templateDescription')}</FieldLabel>
          <LimitedTextInput
            value={form.description}
            maxLength={FIELD_MAX_LENGTH.description}
            onChange={(v) => update('description', v)}
          />
        </div>

        <SectionTitle>{t('serviceConfigTemplate.sectionContainer')}</SectionTitle>
        <div className="md:col-span-2">
          {textField('agent_image', t('serviceConfigTemplate.agentImage'), {
            maxLength: FIELD_MAX_LENGTH.agent_image,
            required: true,
          })}
        </div>
        {textField('container_name', t('serviceConfigTemplate.containerName'), {
          maxLength: FIELD_MAX_LENGTH.container_name,
          required: true,
        })}
        {textField('pod_name', t('serviceConfigTemplate.podName'), {
          maxLength: FIELD_MAX_LENGTH.pod_name,
        })}
        {numField('container_port', t('serviceConfigTemplate.containerPort'))}
        {textField('port_name', t('serviceConfigTemplate.portName'), {
          maxLength: FIELD_MAX_LENGTH.port_name,
        })}
        <div>
          <label className="label">{t('serviceConfigTemplate.imagePullPolicy')}</label>
          <select
            className="select"
            value={form.image_pull_policy}
            onChange={(e) => update('image_pull_policy', e.target.value)}
          >
            <option value="IfNotPresent">IfNotPresent</option>
            <option value="Always">Always</option>
            <option value="Never">Never</option>
          </select>
        </div>
        {textField('kubeconfig', t('serviceConfigTemplate.kubeconfig'), {
          maxLength: FIELD_MAX_LENGTH.kubeconfig,
        })}

        <SectionTitle>{t('serviceConfigTemplate.sectionReadiness')}</SectionTitle>
        {numField('readiness_initial_delay', t('serviceConfigTemplate.readinessInitialDelay'))}
        {numField('readiness_period', t('serviceConfigTemplate.readinessPeriod'))}
        {numField('ready_timeout', t('serviceConfigTemplate.readyTimeout'))}
        {numField('ready_poll_interval', t('serviceConfigTemplate.readyPollInterval'))}

        <SectionTitle>{t('serviceConfigTemplate.sectionNfs')}</SectionTitle>
        <div className="md:col-span-2">
          {textField('nfs_server', t('serviceConfigTemplate.nfsServer'), {
            maxLength: FIELD_MAX_LENGTH.nfs_server,
          })}
        </div>
        {textField('nfs_path', t('serviceConfigTemplate.nfsPath'), {
          maxLength: FIELD_MAX_LENGTH.nfs_path,
        })}
        {textField('nfs_mount_path', t('serviceConfigTemplate.nfsMountPath'), {
          maxLength: FIELD_MAX_LENGTH.nfs_mount_path,
        })}

        <SectionTitle>{t('serviceConfigTemplate.sectionResources')}</SectionTitle>
        {textField('agent_cpu_request', t('serviceConfigTemplate.agentCpuRequest'), {
          maxLength: FIELD_MAX_LENGTH.agent_cpu_request,
          placeholder: '500m',
        })}
        {textField('agent_memory_request', t('serviceConfigTemplate.agentMemoryRequest'), {
          maxLength: FIELD_MAX_LENGTH.agent_memory_request,
          placeholder: '512Mi',
        })}
        {textField('agent_cpu_limit', t('serviceConfigTemplate.agentCpuLimit'), {
          maxLength: FIELD_MAX_LENGTH.agent_cpu_limit,
          placeholder: '2',
        })}
        {textField('agent_memory_limit', t('serviceConfigTemplate.agentMemoryLimit'), {
          maxLength: FIELD_MAX_LENGTH.agent_memory_limit,
          placeholder: '2Gi',
        })}
        {textField('jiuwenbox_cpu_request', t('serviceConfigTemplate.jiuwenboxCpuRequest'), {
          maxLength: FIELD_MAX_LENGTH.jiuwenbox_cpu_request,
          placeholder: '250m',
        })}
        {textField('jiuwenbox_memory_request', t('serviceConfigTemplate.jiuwenboxMemoryRequest'), {
          maxLength: FIELD_MAX_LENGTH.jiuwenbox_memory_request,
          placeholder: '256Mi',
        })}
        {textField('jiuwenbox_cpu_limit', t('serviceConfigTemplate.jiuwenboxCpuLimit'), {
          maxLength: FIELD_MAX_LENGTH.jiuwenbox_cpu_limit,
          placeholder: '1',
        })}
        {textField('jiuwenbox_memory_limit', t('serviceConfigTemplate.jiuwenboxMemoryLimit'), {
          maxLength: FIELD_MAX_LENGTH.jiuwenbox_memory_limit,
          placeholder: '1Gi',
        })}

        <SectionTitle>{t('serviceConfigTemplate.sectionPool')}</SectionTitle>
        {numField('min_idle_services', t('serviceConfigTemplate.minIdleServices'))}
        {numField('max_services', t('serviceConfigTemplate.maxServices'))}
        {numField('service_concurrency', t('serviceConfigTemplate.serviceConcurrency'))}
        {numField('service_ttl', t('serviceConfigTemplate.serviceTtl'))}
        {numField('autoscale_interval', t('serviceConfigTemplate.autoscaleInterval'), 0.1)}
        {numField('message_timeout', t('serviceConfigTemplate.messageTimeout'))}
        {numField('session_concurrency', t('serviceConfigTemplate.sessionConcurrency'))}
        {numField('session_ttl', t('serviceConfigTemplate.sessionTtl'))}
      </div>
    </Modal>
  );
}
