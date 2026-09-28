/**
 * Auth hook — one place to read the current Supabase session and drive
 * sign-in / sign-up / sign-out.
 *
 * Consumers should treat `session` as the source of truth:
 *   * `null`         — signed out
 *   * `Session`      — signed in, has JWT for API calls
 *   * `loading` true — we're still hydrating from SecureStore
 */
import { useEffect, useState } from 'react';
import { Platform } from 'react-native';
import type { AuthError, Session, User } from '@supabase/supabase-js';
import { supabase } from '@/lib/supabase';
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
  signInWithPassword: (email: string, password: string) => Promise<{ error: AuthError | null }>;
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

export function useAuth(): UseAuthResult {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    // Hydrate the initial session from SecureStore. Supabase JS does
    // this synchronously from its internal cache after the first
    // getSession() call succeeds.
    supabase.auth.getSession().then(({ data }) => {
      setSession(data.session);
      setLoading(false);
    });

    // Subscribe to token refreshes, sign-in, sign-out — the callback
    // fires whether the change originated from this hook or any other
    // path (e.g. the OAuth deep-link handler).
    const { data: sub } = supabase.auth.onAuthStateChange(async (event, next) => {
      setSession(next);
      setLoading(false);
      // Analytics identity: Supabase user id only, never email/metadata.
      // Reset on sign-out so the next user starts clean.
      if (event === 'SIGNED_OUT' || !next) {
        resetAnalytics();
      } else if (event === 'SIGNED_IN' && next.user) {
        identifyUser(next.user.id);
      }
      // OAuth providers overwrite user_metadata with the provider profile
      // on every sign-in. If the user explicitly chose a different name or
      // avatar before, re-apply it so the provider never silently resets
      // them. (USER_UPDATED from our own write below doesn't re-trigger
      // this.) Server-side custom_name wins over the device override so a
      // reinstall can't resurrect a stale local choice.
      if (event === 'SIGNED_IN' && next?.user?.email) {
        const email = next.user.email;
        const meta = next.user.user_metadata ?? {};
        const [rememberedName, rememberedAvatar] = await Promise.all([
          getRememberedDisplayName(email),
          getRememberedAvatarChoice(email),
        ]);
        const wantedName = getCustomName(next.user) ?? rememberedName;
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
      }
    });

    return () => {
      sub.subscription.unsubscribe();
    };
  }, []);

  return {
    session,
    user: session?.user ?? null,
    loading,
    signInWithPassword: async (email, password) => {
      const { error } = await supabase.auth.signInWithPassword({ email, password });
      return { error };
    },
    signUpWithPassword: async (email, password, displayName) => {
      // emailRedirectTo brings the confirmation-link tap back into the
      // app (deep link on native, origin on web) instead of stranding
      // the user on Supabase's Site URL. Our auth/callback screen
      // exchanges the code and routes via the stored oauth-next flag.
      const emailRedirectTo =
        Platform.OS === 'web' && typeof window !== 'undefined'
          ? `${window.location.origin}`
          : buildOAuthRedirectUrl();
      // custom_name mirrors the choice server-side: providers overwrite
      // full_name but never this key, so the choice survives reinstalls.
      const nameData = displayName
        ? { full_name: displayName, custom_name: displayName }
        : undefined;
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: {
          ...(nameData ? { data: nameData } : undefined),
          emailRedirectTo,
        },
      });
      return { error, session: data.session, user: data.user };
    },
    signInWithOAuth: async (provider, next = 'login') => {
      return performOAuthSignIn(provider, next);
    },
    signOut: async () => {
      await supabase.auth.signOut();
    },
  };
}
