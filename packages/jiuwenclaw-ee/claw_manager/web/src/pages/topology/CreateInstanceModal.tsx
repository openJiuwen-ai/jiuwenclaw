import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Modal } from '../../components/Modal';
import { JsonField, tryParseJson, useInvalidJsonChecker } from '../../components/JsonField';
import { InstanceApi, ApiError } from '../../services/api';
import { toast } from '../../stores/uiStore';

interface Props {
  open: boolean;
  onClose: () => void;
  onCreated: () => void;
}

const DEFAULT_AUTH = '{\n  "kubeconfig": ""\n}';

export function CreateInstanceModal({ open, onClose, onCreated }: Props) {
  const { t } = useTranslation();
  const checkJson = useInvalidJsonChecker();

  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [masterHost, setMasterHost] = useState('');
  const [authType, setAuthType] = useState('kubeconfig');
  const [namespace, setNamespace] = useState('default');
  const [groupId, setGroupId] = useState('default');
  const [spaceId, setSpaceId] = useState('default');
  const [creatorId, setCreatorId] = useState('system');
  const [managementApiBase, setManagementApiBase] = useState('http://127.0.0.1:18080');
  const [authConfig, setAuthConfig] = useState(DEFAULT_AUTH);
  const [resourceQuota, setResourceQuota] = useState('');
  const [saving, setSaving] = useState(false);

  const reset = () => {
    setName('');
    setDescription('');
    setMasterHost('');
    setAuthType('kubeconfig');
    setNamespace('default');
    setGroupId('default');
    setSpaceId('default');
    setCreatorId('system');
    setManagementApiBase('http://127.0.0.1:18080');
    setAuthConfig(DEFAULT_AUTH);
    setResourceQuota('');
  };

  const submit = async () => {
    if (!name.trim()) {
      toast('warn', t('instanceForm.name'));
      return;
    }
    const authErr = checkJson(authConfig);
    const quotaErr = checkJson(resourceQuota);
    if (authErr) {
      toast('danger', authErr);
      return;
    }
    if (quotaErr) {
      toast('danger', quotaErr);
      return;
    }
    setSaving(true);
    try {
      await InstanceApi.create({
        jiuwenclaw_name: name.trim(),
        description: description.trim() || undefined,
        k8s_master_host: masterHost.trim(),
        k8s_auth_type: authType.trim(),
        k8s_auth_config: tryParseJson(authConfig, {}),
        k8s_namespace: namespace.trim() || 'default',
        resource_quota: resourceQuota.trim() ? tryParseJson(resourceQuota, undefined) : undefined,
        creator_id: creatorId.trim() || 'system',
        group_id: groupId.trim() || 'default',
        space_id: spaceId.trim() || 'default',
        management_api_base: managementApiBase.trim() || undefined,
      });
      toast('success', t('success.created'));
      reset();
      onCreated();
    } catch (e) {
      toast('danger', t('errors.saveFailed', { detail: e instanceof ApiError ? e.detail : (e as Error).message }));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal
      open={open}
      title={t('topology.createInstance')}
      onClose={onClose}
      size="lg"
      footer={
        <>
          <button className="btn ghost" onClick={onClose}>
            {t('common.cancel')}
          </button>
          <button className="btn primary" onClick={submit} disabled={saving}>
            {saving ? t('common.loading') : t('common.submit')}
          </button>
        </>
      }
    >
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="label">{t('instanceForm.name')}</label>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div>
          <label className="label">{t('instanceForm.creatorId')}</label>
          <input className="input" value={creatorId} onChange={(e) => setCreatorId(e.target.value)} />
        </div>
        <div className="md:col-span-2">
          <label className="label">{t('instanceForm.description')}</label>
          <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} />
        </div>
        <div>
          <label className="label">{t('instanceForm.k8sMasterHost')}</label>
          <input
            className="input"
            value={masterHost}
            onChange={(e) => setMasterHost(e.target.value)}
            placeholder="https://1.2.3.4:6443"
          />
        </div>
        <div>
          <label className="label">{t('instanceForm.k8sAuthType')}</label>
          <input className="input" value={authType} onChange={(e) => setAuthType(e.target.value)} />
        </div>
        <div>
          <label className="label">{t('instanceForm.k8sNamespace')}</label>
          <input className="input" value={namespace} onChange={(e) => setNamespace(e.target.value)} />
        </div>
        <div>
          <label className="label">{t('instanceForm.managementApiBase')}</label>
          <input
            className="input"
            value={managementApiBase}
            onChange={(e) => setManagementApiBase(e.target.value)}
          />
        </div>
        <div>
          <label className="label">{t('instanceForm.groupId')}</label>
          <input className="input" value={groupId} onChange={(e) => setGroupId(e.target.value)} />
        </div>
        <div>
          <label className="label">{t('instanceForm.spaceId')}</label>
          <input className="input" value={spaceId} onChange={(e) => setSpaceId(e.target.value)} />
        </div>
        <div className="md:col-span-2">
          <JsonField
            label={t('instanceForm.k8sAuthConfig')}
            value={authConfig}
            onChange={setAuthConfig}
            rows={5}
          />
        </div>
        <div className="md:col-span-2">
          <JsonField
            label={t('instanceForm.resourceQuota')}
            value={resourceQuota}
            onChange={setResourceQuota}
            placeholder="{}"
            rows={4}
          />
        </div>
      </div>
    </Modal>
  );
}
