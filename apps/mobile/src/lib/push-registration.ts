/** Push permission and owner-scoped token lifecycle helpers. */
import * as SecureStore from 'expo-secure-store';
import { Platform } from 'react-native';
import { api } from '@/lib/api';

export interface NotificationPermission {
  granted: boolean;
  canAskAgain?: boolean;
  status?: string;
}

export interface PushNotificationDriver {
  getPermissionsAsync(): Promise<NotificationPermission>;
  requestPermissionsAsync(): Promise<NotificationPermission>;
  getExpoPushTokenAsync(options?: { projectId?: string }): Promise<{ data: string }>;
}

export type PushRegistrationResult =
  | { status: 'registered' }
  | { status: 'unregistered' }
  | { status: 'permission_denied' }
  | { status: 'permission_unavailable' }
  | { status: 'failed' };

type PushPlatform = 'expo';
interface StoredPushTokens {
  /** Current token, retained until the server confirms it was unregistered. */
  registeredToken?: string;
  /** Token staged before registration, covering a lost response or process exit. */
  pendingToken?: string;
}

const ownerTokenLocks = new Map<string, Promise<void>>();

export async function withPushOwnerTransitionLock<T>(ownerId: string, operation: () => Promise<T>): Promise<T> {
  const previous = ownerTokenLocks.get(ownerId) ?? Promise.resolve();
  let release!: () => void;
  const gate = new Promise<void>((resolve) => { release = resolve; });
  const queued = previous.then(() => gate);
  ownerTokenLocks.set(ownerId, queued);
  await previous;
  try {
    return await operation();
  } finally {
    release();
    if (ownerTokenLocks.get(ownerId) === queued) ownerTokenLocks.delete(ownerId);
  }
}

function storageKey(ownerId: string): string {
  return `spillthereel.push.${ownerId}`;
}

async function readStoredTokens(ownerId: string): Promise<StoredPushTokens> {
  if (!ownerId || Platform.OS === 'web') return {};
  try {
    const value = await SecureStore.getItemAsync(storageKey(ownerId));
    if (!value) return {};
    const parsed = JSON.parse(value) as StoredPushTokens;
    return {
      ...(typeof parsed.registeredToken === 'string' ? { registeredToken: parsed.registeredToken } : {}),
      ...(typeof parsed.pendingToken === 'string' ? { pendingToken: parsed.pendingToken } : {}),
    };
  } catch {
    // Treat unreadable storage as unavailable; callers fail closed before auth changes.
    throw new Error('push_storage_unavailable');
  }
}

async function writeStoredTokens(ownerId: string, value: StoredPushTokens): Promise<void> {
  if (!ownerId || Platform.OS === 'web') return;
  try {
    if (!value.registeredToken && !value.pendingToken) {
      await SecureStore.deleteItemAsync(storageKey(ownerId));
    } else {
      await SecureStore.setItemAsync(storageKey(ownerId), JSON.stringify(value));
    }
  } catch {
    throw new Error('push_storage_unavailable');
  }
}

/** Ask the OS only from an explicit user action while permission remains askable. */
export async function requestPushPermission(
  driver: Pick<PushNotificationDriver, 'getPermissionsAsync' | 'requestPermissionsAsync'>,
): Promise<'granted' | 'denied' | 'unavailable'> {
  try {
    const current = await driver.getPermissionsAsync();
    if (current.granted) return 'granted';
    if (current.canAskAgain === false) return 'denied';
    const requested = await driver.requestPermissionsAsync();
    return requested.granted ? 'granted' : 'denied';
  } catch {
    return 'unavailable';
  }
}

/** Register and atomically rotate; persist the candidate before network I/O for cleanup after interruption. */
export async function registerPushToken(
  driver: Pick<PushNotificationDriver, 'getPermissionsAsync' | 'getExpoPushTokenAsync'>,
  ownerId: string,
  options: { previousToken?: string; projectId?: string } = {},
): Promise<PushRegistrationResult> {
  if (!ownerId) return { status: 'failed' };
  return withPushOwnerTransitionLock(ownerId, async () => {
    try {
      const permission = await driver.getPermissionsAsync();
      if (!permission.granted) {
        return { status: permission.canAskAgain === false ? 'permission_denied' : 'permission_unavailable' };
      }

      const { data: token } = await driver.getExpoPushTokenAsync(
        options.projectId ? { projectId: options.projectId } : undefined,
      );
      if (!token) return { status: 'failed' };

      const stored = await readStoredTokens(ownerId);
      const previousToken = options.previousToken ?? stored.registeredToken;
      await writeStoredTokens(ownerId, { ...stored, pendingToken: token });
      await api.post<{ ok: boolean }>(
        '/v1/notifications/token',
        {
          token,
          platform: 'expo' satisfies PushPlatform,
          ...(previousToken && previousToken !== token ? { previous_token: previousToken } : {}),
        },
        { expectedUserId: ownerId },
      );

      // If this write fails, the pre-network pending record remains available for revocation.
      await writeStoredTokens(ownerId, { registeredToken: token });
      return { status: 'registered' };
    } catch {
      return { status: 'failed' };
    }
  });
}

/** Request permission, register, and optionally rotate the previously stored token. */
export async function requestAndRegisterPush(
  driver: PushNotificationDriver,
  ownerId: string,
  options: { previousToken?: string; projectId?: string } = {},
): Promise<PushRegistrationResult> {
  const permission = await requestPushPermission(driver);
  if (permission === 'denied') return { status: 'permission_denied' };
  if (permission === 'unavailable') return { status: 'permission_unavailable' };
  return registerPushToken(driver, ownerId, options);
}

/** Unregister one explicit token; successful revocation is required before local deletion. */
export async function unregisterPushToken(
  token: string,
  ownerId: string,
): Promise<PushRegistrationResult> {
  if (!ownerId || !token) return { status: 'failed' };
  try {
    await api.post<{ ok: boolean }>(
      '/v1/notifications/token/unregister',
      { token, platform: 'expo' satisfies PushPlatform },
      { expectedUserId: ownerId },
    );
    return { status: 'unregistered' };
  } catch {
    return { status: 'failed' };
  }
}

/** Revoke every known token for this owner; keep SecureStore state if any call fails. */
export async function unregisterOwnerPushTokens(ownerId: string): Promise<PushRegistrationResult> {
  if (!ownerId) return { status: 'failed' };
  return withPushOwnerTransitionLock(ownerId, () => unregisterOwnerPushTokensUnderLock(ownerId));
}

/** Caller must hold withPushOwnerTransitionLock(ownerId) for the whole operation. */
export async function unregisterOwnerPushTokensUnderLock(ownerId: string): Promise<PushRegistrationResult> {
  if (!ownerId) return { status: 'failed' };
  let stored: StoredPushTokens;
  try {
    stored = await readStoredTokens(ownerId);
  } catch {
    return { status: 'failed' };
  }
  const tokens = [...new Set([stored.registeredToken, stored.pendingToken].filter((value): value is string => !!value))];
  if (tokens.length === 0) return { status: 'unregistered' };

  for (const token of tokens) {
    const result = await unregisterPushToken(token, ownerId);
    if (result.status !== 'unregistered') return result;
  }
  try {
    await writeStoredTokens(ownerId, {});
    return { status: 'unregistered' };
  } catch {
    // Revocation has succeeded; retaining the local record makes retry safe.
    return { status: 'failed' };
  }
}

export async function hasRegisteredPushToken(ownerId: string): Promise<boolean> {
  const stored = await readStoredTokens(ownerId);
  return !!(stored.registeredToken || stored.pendingToken);
}
