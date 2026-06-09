import { useEffect, useState, type ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { ServiceConfigTemplateApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';
import { safeStringify } from '../../utils/format';
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
  namespace: string;
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
  data: string;
  enabled: boolean;
}

const empty: FormState = {
  template_name: '',
  description: '',
  agent_image: '',
  namespace: 'jiuwenclaw',
  pod_name: '',
  container_name: 'agent-server',
  container_port: 8080,
  port_name: 'http',
  image_pull_policy: 'IfNotPresent',
  replicas: 1,
  kubeconfig: '',
  agent_runtime: '',
  readiness_initial_delay: 5,
  readiness_period: 10,
  ready_timeout: 300,
  ready_poll_interval: 2,
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
  max_services: 10,
  service_concurrency: 10,
  service_ttl: 30,
  autoscale_interval: 0.2,
  message_timeout: 300,
  session_concurrency: 10,
  session_ttl: 20,
  data: '',
  enabled: true,
};

function opt(v: string) {
  return v.trim() || undefined;
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
  const checkJson = useInvalidJsonChecker();
  const [form, setForm] = useState<FormState>(empty);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!open) return;
    if (template) {
      setForm({
        template_name: template.template_name,
        description: template.description ?? '',
        agent_image: template.agent_image,
        namespace: template.namespace,
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
      toast('warn', t('serviceConfigTemplate.templateName'));
      return;
    }
    if (!form.agent_image.trim()) {
      toast('warn', t('serviceConfigTemplate.agentImage'));
      return;
    }
    if (!form.namespace.trim()) {
      toast('warn', t('serviceConfigTemplate.namespace'));
      return;
    }
    if (!form.container_name.trim()) {
      toast('warn', t('serviceConfigTemplate.containerName'));
      return;
    }
    const dataErr = checkJson(form.data);
    if (dataErr) {
      toast('danger', dataErr);
      return;
    }

    const body: ServiceConfigTemplateCreateBody | ServiceConfigTemplateUpdateBody = {
      template_name: form.template_name.trim(),
      description: opt(form.description),
      agent_image: form.agent_image.trim(),
      namespace: form.namespace.trim(),
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
      data: form.data.trim() ? (tryParseJson(form.data, {}) as Record<string, unknown>) : undefined,
      enabled: form.enabled,
    };

    setSaving(true);
    try {
      if (template) {
        await ServiceConfigTemplateApi.update(template.template_id, body);
      } else {
        await ServiceConfigTemplateApi.create(body as ServiceConfigTemplateCreateBody);
      }
      toast('success', t('success.saved'));
      onSaved();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  const numField = (key: keyof FormState, label: string, min = 0, step?: number) => (
    <div>
      <label className="label">{label}</label>
      <input
        className="input"
        type="number"
        min={min}
        step={step}
        value={form[key] as number}
        onChange={(e) => update(key, Number(e.target.value) as FormState[typeof key])}
      />
    </div>
  );

  const textField = (key: keyof FormState, label: string, placeholder?: string) => (
    <div>
      <label className="label">{label}</label>
      <input
        className="input"
        placeholder={placeholder}
        value={form[key] as string}
        onChange={(e) => update(key, e.target.value as FormState[typeof key])}
      />
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
          <label className="label">{t('serviceConfigTemplate.templateName')}</label>
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

        <SectionTitle>{t('serviceConfigTemplate.sectionContainer')}</SectionTitle>
        {textField('agent_image', t('serviceConfigTemplate.agentImage'))}
        {textField('namespace', t('serviceConfigTemplate.namespace'))}
        {textField('pod_name', t('serviceConfigTemplate.podName'))}
        {textField('container_name', t('serviceConfigTemplate.containerName'))}
        {numField('container_port', t('serviceConfigTemplate.containerPort'), 1)}
        {textField('port_name', t('serviceConfigTemplate.portName'))}
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
        {numField('replicas', t('serviceConfigTemplate.replicas'), 1)}
        {textField('kubeconfig', t('serviceConfigTemplate.kubeconfig'))}
        {textField('agent_runtime', t('serviceConfigTemplate.agentRuntime'))}

        <SectionTitle>{t('serviceConfigTemplate.sectionReadiness')}</SectionTitle>
        {numField('readiness_initial_delay', t('serviceConfigTemplate.readinessInitialDelay'))}
        {numField('readiness_period', t('serviceConfigTemplate.readinessPeriod'), 1)}
        {numField('ready_timeout', t('serviceConfigTemplate.readyTimeout'), 1)}
        {numField('ready_poll_interval', t('serviceConfigTemplate.readyPollInterval'), 1)}

        <SectionTitle>{t('serviceConfigTemplate.sectionNfs')}</SectionTitle>
        {textField('nfs_server', t('serviceConfigTemplate.nfsServer'))}
        {textField('nfs_path', t('serviceConfigTemplate.nfsPath'))}
        <div className="md:col-span-2">
          {textField('nfs_mount_path', t('serviceConfigTemplate.nfsMountPath'))}
        </div>

        <SectionTitle>{t('serviceConfigTemplate.sectionResources')}</SectionTitle>
        {textField('agent_cpu_request', t('serviceConfigTemplate.agentCpuRequest'), '500m')}
        {textField('agent_memory_request', t('serviceConfigTemplate.agentMemoryRequest'), '512Mi')}
        {textField('agent_cpu_limit', t('serviceConfigTemplate.agentCpuLimit'), '2')}
        {textField('agent_memory_limit', t('serviceConfigTemplate.agentMemoryLimit'), '2Gi')}
        {textField('jiuwenbox_cpu_request', t('serviceConfigTemplate.jiuwenboxCpuRequest'), '250m')}
        {textField('jiuwenbox_memory_request', t('serviceConfigTemplate.jiuwenboxMemoryRequest'), '256Mi')}
        {textField('jiuwenbox_cpu_limit', t('serviceConfigTemplate.jiuwenboxCpuLimit'), '1')}
        {textField('jiuwenbox_memory_limit', t('serviceConfigTemplate.jiuwenboxMemoryLimit'), '1Gi')}

        <SectionTitle>{t('serviceConfigTemplate.sectionPool')}</SectionTitle>
        {numField('min_idle_services', t('serviceConfigTemplate.minIdleServices'))}
        {numField('max_services', t('serviceConfigTemplate.maxServices'), 1)}
        {numField('service_concurrency', t('serviceConfigTemplate.serviceConcurrency'), 1)}
        {numField('service_ttl', t('serviceConfigTemplate.serviceTtl'), 1)}
        {numField('autoscale_interval', t('serviceConfigTemplate.autoscaleInterval'), 0, 0.1)}
        {numField('message_timeout', t('serviceConfigTemplate.messageTimeout'), 1)}
        {numField('session_concurrency', t('serviceConfigTemplate.sessionConcurrency'), 1)}
        {numField('session_ttl', t('serviceConfigTemplate.sessionTtl'), 1)}

        <SectionTitle>{t('serviceConfigTemplate.sectionExtra')}</SectionTitle>
        <div className="md:col-span-2">
          <JsonField
            label={t('serviceConfigTemplate.data')}
            value={form.data}
            onChange={(v) => update('data', v)}
            rows={4}
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
