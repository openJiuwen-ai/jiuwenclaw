// Copyright (c) Huawei Technologies Co., Ltd. 2025-2026. All rights reserved.

import type { WebConnectionState } from '../../types';
import type { SettingsRequest } from './services/settingsContract';
import type { ExternalCliAgentKind, ExternalCliDetectResult } from '../../components/ExternalCliAgentsSection';
import type { ExternalCliInstallStatuses } from '../../components/ExternalCliInstallDialog';
import { SettingsPageLayout } from './SettingsPageLayout';
import { SettingsServicesProvider } from './services/SettingsServicesProvider';
import type { SettingsPageDefinition } from './registry/types';
import type { SettingsModuleTarget } from './settingsNavigation';

export function SettingsPage({
  definition,
  isConnected,
  connectionState,
  request,
  onHasChangesChange,
  onDetectExternalCli,
  onSelectExternalCliPath,
  onTrackExternalCliDependencyInstalls,
  externalCliInstallStatuses,
  externalCliInstallBusy,
  onOpenExternalCliInstallDialog,
  initialModuleId,
}: {
  definition: SettingsPageDefinition;
  isConnected: boolean;
  connectionState: WebConnectionState;
  request: SettingsRequest;
  onHasChangesChange?: (hasChanges: boolean) => void;
  onDetectExternalCli?: (agent: ExternalCliAgentKind, path?: string) => Promise<ExternalCliDetectResult>;
  onSelectExternalCliPath?: (agent: ExternalCliAgentKind, initialPath?: string) => Promise<string | null>;
  onTrackExternalCliDependencyInstalls?: (statuses: ExternalCliInstallStatuses) => void;
  externalCliInstallStatuses?: ExternalCliInstallStatuses;
  externalCliInstallBusy?: boolean;
  onOpenExternalCliInstallDialog?: () => void;
  initialModuleId?: SettingsModuleTarget;
}) {
  return (
    <SettingsServicesProvider
      isConnected={isConnected}
      connectionState={connectionState}
      request={request}
      onHasChangesChange={onHasChangesChange}
      onDetectExternalCli={onDetectExternalCli}
      onSelectExternalCliPath={onSelectExternalCliPath}
      onTrackExternalCliDependencyInstalls={onTrackExternalCliDependencyInstalls}
      externalCliInstallStatuses={externalCliInstallStatuses}
      externalCliInstallBusy={externalCliInstallBusy}
      onOpenExternalCliInstallDialog={onOpenExternalCliInstallDialog}
    >
      <SettingsPageLayout definition={definition} initialModuleId={initialModuleId} />
    </SettingsServicesProvider>
  );
}
