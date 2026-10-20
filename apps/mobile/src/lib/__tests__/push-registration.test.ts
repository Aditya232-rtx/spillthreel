jest.mock('@/lib/api', () => ({
  api: {
    post: jest.fn(),
  },
}));

const mockSecureValues = new Map<string, string>();
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async (key: string) => mockSecureValues.get(key) ?? null),
  setItemAsync: jest.fn(async (key: string, value: string) => { mockSecureValues.set(key, value); }),
  deleteItemAsync: jest.fn(async (key: string) => { mockSecureValues.delete(key); }),
}));

import { api } from '@/lib/api';
import {
  registerPushToken,
  requestAndRegisterPush,
  requestPushPermission,
  unregisterPushToken,
  unregisterOwnerPushTokens,
  unregisterOwnerPushTokensUnderLock,
  withPushOwnerTransitionLock,
  type PushNotificationDriver,
} from '@/lib/push-registration';

const post = api.post as jest.Mock;
const token = 'ExponentPushToken[private-device-token]';

function driver(
  permission: { granted: boolean; canAskAgain?: boolean } = { granted: true },
): PushNotificationDriver {
  return {
    getPermissionsAsync: jest.fn().mockResolvedValue(permission),
    requestPermissionsAsync: jest.fn().mockResolvedValue(permission),
    getExpoPushTokenAsync: jest.fn().mockResolvedValue({ data: token }),
  };
}

describe('push registration', () => {
  beforeEach(() => {
    post.mockReset().mockResolvedValue({ ok: true });
    mockSecureValues.clear();
  });

  it('requests permission when allowed and reports denial without registering', async () => {
    const notifications = driver({ granted: false, canAskAgain: true });
    notifications.requestPermissionsAsync = jest.fn().mockResolvedValue({ granted: false });

    await expect(requestPushPermission(notifications)).resolves.toBe('denied');
    await expect(requestAndRegisterPush(notifications, 'owner-1')).resolves.toEqual({
      status: 'permission_denied',
    });
    expect(notifications.requestPermissionsAsync).toHaveBeenCalledTimes(2);
    expect(notifications.getExpoPushTokenAsync).not.toHaveBeenCalled();
    expect(post).not.toHaveBeenCalled();
  });

  it('does not prompt again after the OS says permission cannot be requested', async () => {
    const notifications = driver({ granted: false, canAskAgain: false });

    await expect(requestPushPermission(notifications)).resolves.toBe('denied');
    expect(notifications.requestPermissionsAsync).not.toHaveBeenCalled();
  });

  it('registers with authenticated owner scope and returns no token', async () => {
    const notifications = driver();

    await expect(registerPushToken(notifications, 'owner-1', { projectId: 'eas-project' }))
      .resolves.toEqual({ status: 'registered' });
    expect(notifications.getExpoPushTokenAsync).toHaveBeenCalledWith({ projectId: 'eas-project' });
    expect(post).toHaveBeenCalledWith(
      '/v1/notifications/token',
      { token, platform: 'expo' },
      { expectedUserId: 'owner-1' },
    );
  });

  it('rotates the token atomically in the owner-scoped registration request', async () => {
    const notifications = driver();

    await expect(registerPushToken(notifications, 'owner-1', { previousToken: 'old-private-token' }))
      .resolves.toEqual({ status: 'registered' });
    expect(post).toHaveBeenCalledTimes(1);
    expect(post).toHaveBeenCalledWith(
      '/v1/notifications/token',
      { token, platform: 'expo', previous_token: 'old-private-token' },
      { expectedUserId: 'owner-1' },
    );
    expect(JSON.parse(mockSecureValues.get('spillthereel.push.owner-1') ?? '{}'))
      .toEqual({ registeredToken: token });
  });

  it('persists a candidate before registration so interrupted requests can be revoked', async () => {
    post.mockRejectedValueOnce(new Error('response interrupted'));

    await expect(registerPushToken(driver(), 'owner-1')).resolves.toEqual({ status: 'failed' });
    expect(JSON.parse(mockSecureValues.get('spillthereel.push.owner-1') ?? '{}'))
      .toEqual({ pendingToken: token });
  });

  it('does not return token data or leak token values through error results', async () => {
    const notifications = driver();
    post.mockRejectedValue(new Error(`rejected ${token}`));

    const result = await registerPushToken(notifications, 'owner-1');
    expect(result).toEqual({ status: 'failed' });
    expect(JSON.stringify(result)).not.toContain(token);
  });

  it('fences unregister requests to the expected owner', async () => {
    await expect(unregisterPushToken(token, 'owner-2')).resolves.toEqual({ status: 'unregistered' });
    expect(post).toHaveBeenCalledWith(
      '/v1/notifications/token/unregister',
      { token, platform: 'expo' },
      { expectedUserId: 'owner-2' },
    );
  });

  it('retains the owner token after interrupted unregister and clears it after retry succeeds', async () => {
    mockSecureValues.set('spillthereel.push.owner-1', JSON.stringify({ registeredToken: token }));
    post.mockRejectedValueOnce(new Error('offline'));

    await expect(unregisterOwnerPushTokens('owner-1')).resolves.toEqual({ status: 'failed' });
    expect(mockSecureValues.has('spillthereel.push.owner-1')).toBe(true);

    post.mockResolvedValueOnce({ ok: true });
    await expect(unregisterOwnerPushTokens('owner-1')).resolves.toEqual({ status: 'unregistered' });
    expect(mockSecureValues.has('spillthereel.push.owner-1')).toBe(false);
  });

  it('holds the owner lock through auth replacement so stale opt-in cannot register before it finishes', async () => {
    mockSecureValues.set('spillthereel.push.owner-1', JSON.stringify({ registeredToken: 'old-token' }));
    const order: string[] = [];
    post.mockImplementation(async (path: string) => {
      order.push(path.endsWith('/unregister') ? 'unregister' : 'register');
      return { ok: true };
    });

    let markRevoked!: () => void;
    const revoked = new Promise<void>((resolve) => { markRevoked = resolve; });
    let releaseTransition!: () => void;
    const holdTransition = new Promise<void>((resolve) => { releaseTransition = resolve; });
    const transition = withPushOwnerTransitionLock('owner-1', async () => {
      await unregisterOwnerPushTokensUnderLock('owner-1');
      markRevoked();
      await holdTransition;
      order.push('auth-replacement-complete');
    });

    await revoked;
    const registration = registerPushToken(driver(), 'owner-1');
    await Promise.resolve();
    await Promise.resolve();
    expect(order).toEqual(['unregister']);

    releaseTransition();
    await transition;
    await expect(registration).resolves.toEqual({ status: 'registered' });
    expect(order).toEqual(['unregister', 'auth-replacement-complete', 'register']);
  });

  it('returns an error-safe result when permission APIs fail', async () => {
    const notifications = driver();
    notifications.getPermissionsAsync = jest.fn().mockRejectedValue(new Error('native unavailable'));

    await expect(requestAndRegisterPush(notifications, 'owner-1')).resolves.toEqual({
      status: 'permission_unavailable',
    });
    expect(post).not.toHaveBeenCalled();
  });
});
