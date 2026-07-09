import { useCallback, useEffect, useState } from 'react';
import type { TabType, TeamDetailTab } from '../components/teamArea/shared';

const TEAM_PANEL_STATE_KEY = 'jiuwenclaw_team_panel_state';
const TEAM_PANEL_STATE_EVENT = 'jiuwenclaw-team-panel-state-change';

export interface TeamPanelState {
  expanded: boolean;
  activeTab: TabType;
  activeDetailTab: TeamDetailTab;
  selectedMemberId?: string;
}

const DEFAULT_TEAM_PANEL_STATE: TeamPanelState = {
  expanded: false,
  activeTab: 'team',
  activeDetailTab: 'members',
};

interface UseTeamPanelStateResult {
  teamAreaExpanded: boolean;
  teamAreaActiveTab: TabType;
  teamAreaActiveDetailTab: TeamDetailTab;
  teamAreaSelectedMemberId?: string;
  setTeamAreaExpanded: (expanded: boolean) => void;
  setTeamAreaActiveTab: (tab: TabType) => void;
  setTeamAreaActiveDetailTab: (tab: TeamDetailTab) => void;
  setTeamAreaSelectedMemberId: (memberId: string) => void;
}

function loadTeamPanelState(): TeamPanelState {
  const raw = window.localStorage.getItem(TEAM_PANEL_STATE_KEY);
  if (!raw) {
    return DEFAULT_TEAM_PANEL_STATE;
  }
  return JSON.parse(raw) as TeamPanelState;
}

function saveTeamPanelState(nextState: TeamPanelState): void {
  window.localStorage.setItem(TEAM_PANEL_STATE_KEY, JSON.stringify(nextState));
}

function notifyTeamPanelState(nextState: TeamPanelState): void {
  window.dispatchEvent(new CustomEvent<TeamPanelState>(TEAM_PANEL_STATE_EVENT, {
    detail: nextState,
  }));
}

export function openTeamPanel(
  activeTab: TabType,
  activeDetailTab: TeamDetailTab = 'members',
  selectedMemberId?: string
): void {
  const nextState = {
    expanded: true,
    activeTab,
    activeDetailTab,
    selectedMemberId,
  };
  saveTeamPanelState(nextState);
  notifyTeamPanelState(nextState);
}

export function useTeamPanelState(): UseTeamPanelStateResult {
  const [state, setState] = useState<TeamPanelState>(loadTeamPanelState);

  const updateState = useCallback((patch: Partial<TeamPanelState>) => {
    setState((current) => {
      const nextState = { ...current, ...patch };
      saveTeamPanelState(nextState);
      notifyTeamPanelState(nextState);
      return nextState;
    });
  }, []);

  const setTeamAreaExpanded = useCallback((expanded: boolean) => {
    updateState({ expanded });
  }, [updateState]);

  const setTeamAreaActiveTab = useCallback((activeTab: TabType) => {
    updateState({ activeTab });
  }, [updateState]);

  const setTeamAreaActiveDetailTab = useCallback((activeDetailTab: TeamDetailTab) => {
    updateState({ activeDetailTab });
  }, [updateState]);

  const setTeamAreaSelectedMemberId = useCallback((selectedMemberId: string) => {
    updateState({ selectedMemberId });
  }, [updateState]);

  useEffect(() => {
    function handleTeamPanelStateChange(event: Event) {
      setState((event as CustomEvent<TeamPanelState>).detail);
    }

    window.addEventListener(TEAM_PANEL_STATE_EVENT, handleTeamPanelStateChange);
    return () => {
      window.removeEventListener(TEAM_PANEL_STATE_EVENT, handleTeamPanelStateChange);
    };
  }, []);

  return {
    teamAreaExpanded: state.expanded,
    teamAreaActiveTab: state.activeTab,
    teamAreaActiveDetailTab: state.activeDetailTab,
    teamAreaSelectedMemberId: state.selectedMemberId,
    setTeamAreaExpanded,
    setTeamAreaActiveTab,
    setTeamAreaActiveDetailTab,
    setTeamAreaSelectedMemberId,
  };
}
