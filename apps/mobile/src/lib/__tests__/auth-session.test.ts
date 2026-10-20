const mockStoredPush = new Map<string, string>();
jest.mock('@/lib/supabase', () => ({
  supabase: { auth: { getSession: jest.fn(), signOut: jest.fn() } },
}));
jest.mock('@/lib/api', () => ({ api: { post: jest.fn() } }));
jest.mock('expo-secure-store', () => ({
  getItemAsync: jest.fn(async (key: string) => mockStoredPush.get(key) ?? null),
  setItemAsync: jest.fn(async (key: string, value: string) => { mockStoredPush.set(key, value); }),
  deleteItemAsync: jest.fn(async (key: string) => { mockStoredPush.delete(key); }),
}));

import { supabase } from '@/lib/supabase';
import { api } from '@/lib/api';
import {
  restorePushIfCurrentOwner,
  runWithPushRevoked,
  signOutCurrentSession,
} from '@/lib/auth-session';

const mockGetSession = supabase.auth.getSession as jest.Mock;
const mockSignOut = supabase.auth.signOut as jest.Mock;
const mockPost = api.post as jest.Mock;
const ownerOneKey = 'spillthereel.push.owner-1';

describe('push-safe auth transitions', () => {
  beforeEach(() => {
    mockStoredPush.clear();
    mockStoredPush.set(ownerOneKey, JSON.stringify({ registeredToken: 'token-1' }));
    mockGetSession.mockReset().mockResolvedValue({
      data: { session: { user: { id: 'owner-1' } } },
      error: null,
    });
    mockSignOut.mockReset().mockResolvedValue({ error: null });
    mockPost.mockReset().mockResolvedValue({ ok: true });
  });

  it('revokes the current owner before an account replacement callback runs', async () => {
    const order: string[] = [];
    mockPost.mockImplementation(async (path: string) => {
      order.push(path.endsWith('/unregister') ? 'revoke' : 'register');
      return { ok: true };
    });

    const restore = jest.fn().mockImplementation(async () => { order.push('restore'); });
    const result = await runWithPushRevoked(async () => {
      order.push('replace-auth');
      return 'done';
    }, restore);

    expect(result).toEqual({ previousOwnerId: 'owner-1', value: 'done' });
    expect(order).toEqual(['revoke', 'replace-auth', 'restore']);
  });

  it('does not begin an account replacement when token revocation is interrupted', async () => {
    const replaceAuth = jest.fn();
    mockPost.mockRejectedValueOnce(new Error('offline'));

    await expect(runWithPushRevoked(replaceAuth, jest.fn())).rejects.toThrow('push_revocation_failed');
    expect(replaceAuth).not.toHaveBeenCalled();
  });

  it('keeps the local session when revocation fails before sign-out', async () => {
    mockPost.mockRejectedValueOnce(new Error('offline'));

    await expect(signOutCurrentSession(jest.fn())).rejects.toThrow('push_revocation_failed');
    expect(mockSignOut).not.toHaveBeenCalled();
  });

  it('leaves notifications disabled for an account that had no registered token', async () => {
    mockStoredPush.clear();

    const action = jest.fn().mockResolvedValue('done');
    const restore = jest.fn();
    const result = await runWithPushRevoked(action, restore);
    expect(result.previousOwnerId).toBeNull();
    expect(action).toHaveBeenCalled();
    expect(mockPost).not.toHaveBeenCalled();
    expect(restore).not.toHaveBeenCalled();
  });

  it('restores an opted-in account after a flow returns without switching sessions', async () => {
    const restore = jest.fn().mockResolvedValue({ status: 'registered' });
    await runWithPushRevoked(async () => ({ error: { name: 'OAuthCancelled' } }), restore);
    expect(restore).toHaveBeenCalledWith('owner-1');
  });

  it('does not restore push after the live session switches to another account', async () => {
    const restore = jest.fn().mockResolvedValue({ status: 'registered' });
    mockGetSession.mockResolvedValue({
      data: { session: { user: { id: 'owner-2' } } },
      error: null,
    });

    await expect(restorePushIfCurrentOwner('owner-1', restore)).resolves.toBe(false);
    expect(restore).not.toHaveBeenCalled();
  });

  it('retries under the new live owner when a queued auth transition captured a stale owner', async () => {
    let liveOwner = 'owner-A';
    let secondStarted = false;
    let signalSecondRead!: () => void;
    const secondRead = new Promise<void>((resolve) => { signalSecondRead = resolve; });
    mockGetSession.mockImplementation(async () => {
      if (secondStarted) signalSecondRead();
      return { data: { session: { user: { id: liveOwner } } }, error: null };
    });

    const order: string[] = [];
    mockStoredPush.set('spillthereel.push.owner-A', JSON.stringify({ registeredToken: 'token-A' }));
    mockStoredPush.set('spillthereel.push.owner-B', JSON.stringify({ registeredToken: 'token-B' }));
    mockPost.mockImplementation(async (path: string, body: { token?: string }) => {
      if (path.endsWith('/unregister')) order.push(`revoke:${body.token}`);
      return { ok: true };
    });

    let signalFirstAction!: () => void;
    const firstActionStarted = new Promise<void>((resolve) => { signalFirstAction = resolve; });
    let releaseFirstAction!: () => void;
    const firstActionGate = new Promise<void>((resolve) => { releaseFirstAction = resolve; });
    const firstTransition = runWithPushRevoked(async () => {
      order.push('first-action-start');
      signalFirstAction();
      await firstActionGate;
      liveOwner = 'owner-B';
      order.push('first-action-finish');
    });

    await firstActionStarted;
    secondStarted = true;
    const queuedTransition = runWithPushRevoked(async () => {
      order.push('replace-B-with-C');
    });
    await secondRead;
    releaseFirstAction();

    await Promise.all([firstTransition, queuedTransition]);
    expect(order).toEqual([
      'revoke:token-A',
      'first-action-start',
      'first-action-finish',
      'revoke:token-B',
      'replace-B-with-C',
    ]);
  });

  it('fails closed when ownership keeps changing during bounded retries', async () => {
    let readCount = 0;
    mockGetSession.mockImplementation(async () => {
      readCount += 1;
      const ownerId = readCount % 2 === 1 ? 'owner-A' : `owner-${readCount}`;
      return { data: { session: { user: { id: ownerId } } }, error: null };
    });
    const action = jest.fn();

    await expect(runWithPushRevoked(action)).rejects.toThrow('auth_owner_changed');
    expect(action).not.toHaveBeenCalled();
    expect(mockPost).not.toHaveBeenCalled();
  });
});
