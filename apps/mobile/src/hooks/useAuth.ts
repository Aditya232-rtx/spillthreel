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
import type { AuthError, Session, User } from '@supabase/supabase-js';
import { supabase } from '@/lib/supabase';
import { performOAuthSignIn } from '@/lib/oauth';

interface UseAuthResult {
  session: Session | null;
  user: User | null;
  loading: boolean;
  signInWithPassword: (email: string, password: string) => Promise<{ error: AuthError | null }>;
  signUpWithPassword: (
    email: string,
    password: string,
    displayName?: string,
  ) => Promise<{ error: AuthError | null; session: Session | null }>;
  signInWithOAuth: (
    provider: 'google' | 'apple',
    next?: 'login' | 'signup',
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
    const { data: sub } = supabase.auth.onAuthStateChange((_event, next) => {
      setSession(next);
      setLoading(false);
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
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
        options: displayName
          ? { data: { full_name: displayName } }
          : undefined,
      });
      return { error, session: data.session };
    },
    signInWithOAuth: async (provider, next = 'login') => {
      return performOAuthSignIn(provider, next);
    },
    signOut: async () => {
      await supabase.auth.signOut();
    },
  };
}
