import { Bell } from 'lucide-react';
import { useTranslation } from 'react-i18next';
import { getAgentAvatarUrl, type AgentCatalogItem } from '../../features/agentManagement';

type DefinitionCardProps = {
  item: AgentCatalogItem;
  scope: 'catalog' | 'mine';
  busy: boolean;
  onOpen: (id: string) => void;
  onUse: (id: string) => void;
  onReconnect: (id: string) => void;
  onInstall: (id: string) => void;
  onUninstall: (id: string) => void;
};

function getAvatarLetter(name: string): string {
  return name.trim().slice(0, 1).toUpperCase() || '?';
}

export function DefinitionCard({ item, scope, busy, onOpen, onUse, onReconnect, onInstall, onUninstall }: DefinitionCardProps) {
  const { t } = useTranslation();
  const canInstall = !item.installed;
  const canUse = item.installed && item.connectionState === 'connected' && item.enabled !== false;
  const needsConnection = item.installed && item.connectionState !== 'connected';
  const avatarUrl = getAgentAvatarUrl(item);

  return (
    <article className={`agent-management-card agent-management-card--${scope}`} data-testid={`agent-card-${item.id}`}>
      <button
        type="button"
        className="agent-management-card__body"
        onClick={() => onOpen(item.id)}
        aria-label={t('agentManagement.card.open', { name: item.displayName })}
      >
        <span className="agent-management-card__heading">
          <span className="agent-management-avatar" aria-hidden="true">
            {avatarUrl ? <img src={avatarUrl} alt="" /> : <span className="agent-management-avatar__letter">{getAvatarLetter(item.displayName)}</span>}
          </span>
          <span className="agent-management-card__content">
            <span className="agent-management-card__title-row">
              <span className="agent-management-card__title">{item.displayName}</span>
              {scope === 'mine' && item.updateAvailable ? (
                <span className="agent-management-card__update">
                  <Bell size={16} aria-hidden="true" />
                  <span className="agent-management-card__update-dot" aria-hidden="true" />
                  <span className="agent-management-card__update-tooltip" role="status">
                    {t('agentManagement.states.newVersion')}
                  </span>
                </span>
              ) : null}
            </span>
            {scope === 'mine' ? (
              <span className="agent-management-card__meta">
                {item.tags.length > 0 ? item.tags.map(tag => (
                  <span key={tag.id} className="agent-management-tag">
                    {tag.label}
                  </span>
                )) : (
                  <span className="agent-management-tag">
                    {t(`agentManagement.categories.${item.category}`, { defaultValue: item.category || t('agentManagement.categoryOther') })}
                  </span>
                )}
              </span>
            ) : item.tags.length > 0 ? (
              <span className="agent-management-card__meta">
                {item.tags.map(tag => (
                  <span key={tag.id} className="agent-management-tag">
                    {tag.label}
                  </span>
                ))}
              </span>
            ) : null}
          </span>
        </span>
        <span className="agent-management-card__description">{item.description || t('agentManagement.unknownDescription')}</span>
      </button>
      <div className="agent-management-card__actions" aria-label={t('agentManagement.card.actions', { name: item.displayName })}>
        {item.installed ? (
          <button
            type="button"
            className="agent-management-button agent-management-button--secondary agent-management-card-action--use"
            disabled={!canUse || busy}
            aria-disabled={!canUse}
            onClick={() => onUse(item.id)}
          >
            {t('agentManagement.actions.use')}
          </button>
        ) : null}
        {canInstall ? (
          <button
            type="button"
            className="agent-management-button agent-management-button--primary"
            disabled={busy}
            aria-busy={busy}
            onClick={() => onInstall(item.id)}
          >
            {busy ? t('agentManagement.actions.installing') : t('agentManagement.actions.install')}
          </button>
        ) : needsConnection ? (
          <button
            type="button"
            className="agent-management-button agent-management-button--secondary"
            disabled={busy}
            aria-busy={busy}
            onClick={() => onReconnect(item.id)}
          >
            {busy ? t('agentManagement.actions.connecting') : t('agentManagement.actions.connect')}
          </button>
        ) : (
          <button
            type="button"
            className="agent-management-button agent-management-button--primary"
            disabled={busy}
            aria-busy={busy}
            onClick={() => onUninstall(item.id)}
          >
            {busy ? t('agentManagement.actions.uninstalling') : t('agentManagement.actions.uninstall')}
          </button>
        )}
      </div>
    </article>
  );
}
