import { supabase } from '@/lib/supabase';
import {
  hasRegisteredPushToken,
  unregisterOwnerPushTokensUnderLock,
  withPushOwnerTransitionLock,
} from '@/lib/push-registration';

/** Restore only while the live session still belongs to the opted-in account. */
export async function restorePushIfCurrentOwner(
  ownerId: string | null,
  restore: (ownerId: string) => Promise<unknown>,
): Promise<boolean> {
  if (!ownerId) return false;
  const { data, error } = await supabase.auth.getSession();
  if (error || data.session?.user.id !== ownerId) return false;
  await restore(ownerId);
  return true;
}

export interface PushTransitionOptions<T> {
  /** Skip restoration after a successful operation such as accepted deletion. */
  restoreOnSuccess?: (value: T) => boolean;
  /** Ambiguous failures may follow a committed server-side operation; default true for auth flows. */
  restoreOnError?: boolean | ((error: unknown) => boolean);
}

/**
 * Hold the per-owner token lock across revocation and the auth operation. A
 * concurrent settings opt-in waits until the old session has been replaced.
 */
export async function runWithPushRevoked<T>(
  action: () => Promise<T>,
  restore?: (ownerId: string) => Promise<unknown>,
  options: PushTransitionOptions<T> = {},
): Promise<{ previousOwnerId: string | null; value: T }> {
  // Another auth transition can change the current owner while this call waits
  // for a lock. Re-read under the lock, release a stale candidate lock, and
  // retry against the live owner. A small bound prevents churn from looping.
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const initial = await supabase.auth.getSession();
    if (initial.error) throw initial.error;
    const candidateOwnerId = initial.data.session?.user.id ?? null;
    if (!candidateOwnerId) return { previousOwnerId: null, value: await action() };

    let wasOptedIn = false;
    let operationOwnerId: string | null = null;
    let value: T;
    try {
      const result = await withPushOwnerTransitionLock(candidateOwnerId, async () => {
        const current = await supabase.auth.getSession();
        if (current.error) throw current.error;
        if (current.data.session?.user.id !== candidateOwnerId) {
          return { retry: true as const };
        }

        wasOptedIn = await hasRegisteredPushToken(candidateOwnerId);
        if (wasOptedIn) {
          const revoked = await unregisterOwnerPushTokensUnderLock(candidateOwnerId);
          if (revoked.status !== 'unregistered') throw new Error('push_revocation_failed');
        }
        operationOwnerId = candidateOwnerId;
        return { retry: false as const, value: await action() };
      });
      if (result.retry) continue;
      operationOwnerId = candidateOwnerId;
      value = result.value;
    } catch (error) {
      const restoreOnError = typeof options.restoreOnError === 'function'
        ? options.restoreOnError(error)
        : options.restoreOnError !== false;
      if (wasOptedIn && operationOwnerId && restoreOnError && restore) {
        await restorePushIfCurrentOwner(operationOwnerId, restore).catch(() => {});
      }
      throw error;
    }

    const shouldRestore = wasOptedIn
      && !!operationOwnerId
      && (options.restoreOnSuccess?.(value) ?? true);
    if (shouldRestore && restore) {
      await restorePushIfCurrentOwner(operationOwnerId, restore).catch(() => {});
    }
    return { previousOwnerId: wasOptedIn && operationOwnerId ? operationOwnerId : null, value };
  }

  throw new Error('auth_owner_changed');
}

/** Sign out only after token revocation; restore on a failed sign-out if this owner remains active. */
export async function signOutCurrentSession(
  restore?: (ownerId: string) => Promise<unknown>,
): Promise<void> {
  const { value } = await runWithPushRevoked(
    () => supabase.auth.signOut({ scope: 'local' }),
    restore,
  );
  if (value.error) throw value.error;
}
