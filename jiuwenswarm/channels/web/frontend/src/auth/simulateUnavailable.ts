import type { EnterpriseAuthProvider } from './types';

// Customer builds bind the virtual simulation provider to this empty adapter.
// This keeps the registry stable while allowing auth/simulate/ to be omitted.
export const simulatedAuthProvider: EnterpriseAuthProvider | null = null;
