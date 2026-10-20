/**
 * Auth hook — one place to read the current Supabase session and drive
 * sign-in / sign-up / sign-out.
 *
 * Consumers should treat `session` as the source of truth:
 *   * `null`         — signed out
 *   * `Session`      — signed in, has JWT for API calls
 *   * `loading` true — we're still hydrating from SecureStore
 */
import { useSyncExternalStore } from 'react';
import { Platform } from 'react-native';
import type { AuthError, Session, User } from '@supabase/supabase-js';
import { supabase } from '@/lib/supabase';
import { runWithPushRevoked, signOutCurrentSession } from '@/lib/auth-session';
import { restoreNativePush } from '@/lib/native-push';
import { identifyUser, resetAnalytics } from '@/lib/analytics';
import { getRememberedAvatarChoice } from '@/lib/avatar';
import { getCustomName, getRememberedDisplayName } from '@/lib/display-name';
import {
  buildOAuthRedirectUrl,
  performOAuthSignIn,
  type OAuthNext,
} from '@/lib/oauth';

interface UseAuthResult {
  session: Session | null;
  user: User | null;
  loading: boolean;
  signInWithPassword: (email: string, password: string) => Promise<{ error: AuthError | null; session: Session | null }>;
  signUpWithPassword: (
    email: string,
    password: string,
    displayName?: string,
  ) => Promise<{ error: AuthError | null; session: Session | null; user: User | null }>;
  signInWithOAuth: (
    provider: 'google' | 'apple',
    next?: OAuthNext,
  ) => Promise<{ error: AuthError | null }>;
  signOut: () => Promise<void>;
}

interface AuthSnapshot {
  session: Session | null;
  loading: boolean;
}

let authSnapshot: AuthSnapshot = { session: null, loading: true };
let authEventRevision = 0;
const authListeners = new Set<() => void>();

function publishAuthSnapshot(session: Session | null, loading: boolean): void {
  if (authSnapshot.session === session && authSnapshot.loading === loading) return;
  authSnapshot = { session, loading };
  authListeners.forEach((listener) => listener());
}

function subscribeToAuthStore(listener: () => void): () => void {
  authListeners.add(listener);
  return () => {
    authListeners.delete(listener);
  };
}

function getAuthSnapshot(): AuthSnapshot {
  return authSnapshot;
}

function transitionBlockedError(): AuthError {
  return {
    name: 'PushRevocationFailed',
    message: 'Could not safely turn off notifications for the signed-in account. Try again before switching accounts.',
    status: 503,
  } as AuthError;
}

// Install one source subscription for the lifetime of this module. Every hook
// consumer reads the same external-store snapshot, including during StrictMode
// remounts, instead of creating another Supabase subscription per component.
supabase.auth.onAuthStateChange((event, next) => {
  authEventRevision += 1;
  publishAuthSnapshot(next, false);

  // Analytics identity: Supabase user id only, never email/metadata.
  if (event === 'SIGNED_OUT' || !next) {
    resetAnalytics();
  } else if (event === 'SIGNED_IN' && next.user) {
    identifyUser(next.user.id);
  }

  // OAuth providers overwrite user_metadata with the provider profile on each
  // sign-in. Keep profile persistence outside the auth callback because Supabase
  // auth methods share an internal lock. Session checks prevent stale work from
  // updating a different account after an account switch.
  if (event === 'SIGNED_IN' && next?.user?.email) {
    const eventSession = next;
    const email = next.user.email;
    void Promise.resolve().then(async () => {
      const [rememberedName, rememberedAvatar] = await Promise.all([
        getRememberedDisplayName(email),
        getRememberedAvatarChoice(email),
      ]);
      if (
        authSnapshot.session?.user.id !== eventSession.user.id ||
        authSnapshot.session.access_token !== eventSession.access_token
      ) return;

      const { data } = await supabase.auth.getSession();
      if (
        authSnapshot.session?.user.id !== eventSession.user.id ||
        authSnapshot.session.access_token !== eventSession.access_token ||
        data.session?.user.id !== eventSession.user.id ||
        data.session.access_token !== eventSession.access_token
      ) return;

      const meta = eventSession.user.user_metadata ?? {};
      const wantedName = getCustomName(eventSession.user) ?? rememberedName;
      const patch: Record<string, string> = {};
      if (wantedName && wantedName !== meta.full_name) {
        patch.full_name = wantedName;
        patch.custom_name = wantedName;
      }
      if (rememberedAvatar && rememberedAvatar !== meta.avatar) {
        patch.avatar = rememberedAvatar;
      }
      if (Object.keys(patch).length > 0) {
        await supabase.auth.updateUser({ data: patch });
      }
    }).catch(() => {
      // Profile reconciliation is best-effort and never blocks auth state.
    });
  }
});

// Subscribe before hydrating so an auth event cannot be missed between the
// initial read and the subscription. Ignore stale hydration after any event.
void supabase.auth.getSession().then(({ data }) => {
  if (authEventRevision === 0) publishAuthSnapshot(data.session, false);
}).catch(() => {
  // Hydration failed (corrupt SecureStore, etc.) — remain signed out.
}).finally(() => {
  if (authSnapshot.loading) publishAuthSnapshot(authSnapshot.session, false);
});

export function useAuth(): UseAuthResult {
  const { session, loading } = useSyncExternalStore(
    subscribeToAuthStore,
    getAuthSnapshot,
    getAuthSnapshot,
  );

  return {
    session,
    user: session?.user ?? null,
    loading,
    signInWithPassword: async (email, password) => {
      try {
        const result = await runWithPushRevoked(
          () => supabase.auth.signInWithPassword({ email, password }),
          restoreNativePush,
        );
        return { error: result.value.error, session: result.value.data.session };
      } catch {
        return { error: transitionBlockedError(), session: null };
      }
    },
    signUpWithPassword: async (email, password, displayName) => {
      try {
        const emailRedirectTo =
          Platform.OS === 'web' && typeof window !== 'undefined'
            ? `${window.location.origin}`
            : buildOAuthRedirectUrl();
        const nameData = displayName
          ? { full_name: displayName, custom_name: displayName }
          : undefined;
        const result = await runWithPushRevoked(() => supabase.auth.signUp({
          email,
          password,
          options: {
            ...(nameData ? { data: nameData } : undefined),
            emailRedirectTo,
          },
        }), restoreNativePush);
        return { error: result.value.error, session: result.value.data.session, user: result.value.data.user };
      } catch {
        return { error: transitionBlockedError(), session: null, user: null };
      }
    },
    signInWithOAuth: async (provider, next = 'login') => {
      try {
        const result = await runWithPushRevoked(
          () => performOAuthSignIn(provider, next),
          restoreNativePush,
        );
        return result.value;
      } catch {
        return { error: transitionBlockedError() };
      }
    },
    signOut: async () => {
      await signOutCurrentSession(restoreNativePush);
    },
  };
}
