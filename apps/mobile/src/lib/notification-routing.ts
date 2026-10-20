export interface ImportSummaryNotificationPayload {
  type: 'import_summary';
  owner_id: string;
  import_id: string;
}

export type ImportSummaryRouteDecision =
  | { status: 'defer' }
  | { status: 'discard' }
  | { status: 'route'; importId: string };

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function isValidOpaqueImportId(value: unknown): value is string {
  return typeof value === 'string'
    && value.length >= 1
    && value.length <= 200
    && !/[\u0000-\u001f\u007f]/.test(value);
}

/** Strictly accept only the server-owned import-summary tap payload contract. */
export function parseImportSummaryNotification(value: unknown): ImportSummaryNotificationPayload | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const payload = value as Record<string, unknown>;
  const keys = Object.keys(payload).sort();
  if (keys.length !== 3 || keys[0] !== 'import_id' || keys[1] !== 'owner_id' || keys[2] !== 'type') return null;
  if (payload.type !== 'import_summary') return null;
  if (typeof payload.owner_id !== 'string' || !UUID_PATTERN.test(payload.owner_id)) return null;
  if (!isValidOpaqueImportId(payload.import_id)) return null;
  return {
    type: 'import_summary',
    owner_id: payload.owner_id,
    import_id: payload.import_id,
  };
}

/** Return a route target only for a well-formed payload owned by the active user. */
export function importSummaryTargetForOwner(
  value: unknown,
  currentOwnerId: string | null | undefined,
): string | null {
  const payload = parseImportSummaryNotification(value);
  if (!payload || !currentOwnerId || payload.owner_id.toLowerCase() !== currentOwnerId.toLowerCase()) return null;
  return payload.import_id;
}

/** Keep valid taps pending until auth exists; discard malformed or other-owner taps. */
export function decideImportSummaryRoute(
  value: unknown,
  currentOwnerId: string | null | undefined,
  authLoading: boolean,
): ImportSummaryRouteDecision {
  if (authLoading) return { status: 'defer' };
  const payload = parseImportSummaryNotification(value);
  if (!payload) return { status: 'discard' };
  if (!currentOwnerId) return { status: 'defer' };
  if (payload.owner_id.toLowerCase() !== currentOwnerId.toLowerCase()) return { status: 'discard' };
  return { status: 'route', importId: payload.import_id };
}

/** Stable per-delivery key for deduplicating cold-start and foreground callbacks. */
export function notificationResponseKey(
  identifier: unknown,
  actionIdentifier: unknown,
  value: unknown,
): string | null {
  if (typeof identifier !== 'string' || !identifier) return null;
  const payload = parseImportSummaryNotification(value);
  if (!payload) return `${identifier}:${String(actionIdentifier ?? '')}:invalid`;
  return `${identifier}:${String(actionIdentifier ?? '')}:${payload.owner_id}:${payload.import_id}`;
}
