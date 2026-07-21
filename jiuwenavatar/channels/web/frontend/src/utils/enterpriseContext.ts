export function isEnterpriseFrontendEnabled(): boolean {
  try {
    return localStorage.getItem('jiuwenavatar_enterprise_enabled') === 'true';
  } catch {
    return false;
  }
}

export function enterpriseUserContext(): Record<string, string> {
  try {
    if (!isEnterpriseFrontendEnabled()) {
      return {};
    }
    const raw = localStorage.getItem('jiuwenavatar_enterprise_user');
    if (!raw) return {};
    const user = JSON.parse(raw) as { sub?: string; group_id?: string; role?: string };
    return {
      ...(user.sub ? { owner_user_id: user.sub, user_id: user.sub } : {}),
      ...(user.group_id ? { group_id: user.group_id } : {}),
      ...(user.role ? { role: user.role } : {}),
    };
  } catch {
    return {};
  }
}

export function isEnterpriseOrgAdmin(user?: { role?: string } | null): boolean {
  const role = (user?.role || enterpriseUserContext().role || '').toLowerCase();
  return ['org_admin', 'orgadmin', 'admin', 'platform_admin', 'tenant_admin', 'group_admin'].includes(role);
}

export function buildEnterpriseUserContext(
  user?: { sub?: string; group_id?: string; role?: string } | null,
): Record<string, string> {
  if (!isEnterpriseFrontendEnabled()) {
    return {};
  }
  if (user?.sub || user?.group_id || user?.role) {
    return {
      ...(user.sub ? { owner_user_id: user.sub, user_id: user.sub } : {}),
      ...(user.group_id ? { group_id: user.group_id } : {}),
      ...(user.role ? { role: user.role } : {}),
    };
  }
  return enterpriseUserContext();
}
