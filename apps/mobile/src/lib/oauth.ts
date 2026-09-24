/**
 * OAuth helper — handles Google and Apple sign in using Supabase Auth
 * and Expo's WebBrowser / makeRedirectUri.
 */
import { makeRedirectUri } from 'expo-auth-session';
import * as WebBrowser from 'expo-web-browser';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';
import { supabase } from '@/lib/supabase';
import type { AuthError } from '@supabase/supabase-js';

// Completes auth session for web / in-app web browser
WebBrowser.maybeCompleteAuthSession();

/**
 * Where to send the user after the OAuth round-trip completes.
 * Stored before opening the browser so app/auth/callback (which runs
 * in a fresh navigation context via deep link) can route correctly.
 * Login → home, signup → import flow.
 */
const OAUTH_NEXT_KEY = 'spillthereel.oauth_next';

export async function setOAuthNext(next: 'login' | 'signup'): Promise<void> {
  try {
    await AsyncStorage.setItem(OAUTH_NEXT_KEY, next);
  } catch {
    // non-fatal: callback falls back to home
  }
}

export async function consumeOAuthNext(): Promise<'login' | 'signup'> {
  try {
    const value = await AsyncStorage.getItem(OAUTH_NEXT_KEY);
    await AsyncStorage.removeItem(OAUTH_NEXT_KEY);
    return value === 'signup' ? 'signup' : 'login';
  } catch {
    return 'login';
  }
}

export function buildOAuthRedirectUrl(): string {
  // Explicit scheme: spillthereel://auth/callback in standalone builds,
  // exp://.../--/auth/callback under Expo Go. This URL MUST be whitelisted
  // in Supabase Dashboard → Authentication → URL Configuration →
  // Redirect URLs, otherwise Supabase falls back to the Site URL and the
  // in-app browser never returns to the app (user picks a Google account
  // then lands back on the login page with no session).
  return makeRedirectUri({
    scheme: 'spillthereel',
    path: 'auth/callback',
  });
}

export async function performOAuthSignIn(
  provider: 'google' | 'apple',
  next: 'login' | 'signup' = 'login',
): Promise<{ error: AuthError | null }> {
  try {
    if (Platform.OS === 'web') {
      const redirectUrl = typeof window !== 'undefined' ? `${window.location.origin}` : undefined;
      console.log('[OAuth] Web redirectTo:', redirectUrl);
      await setOAuthNext(next);
      const { data, error } = await supabase.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: redirectUrl,
        },
      });
      // supabase-js performs window.location.assign(data.url) itself here —
      // this page is about to unload for Google. Log so a missing
      // redirect (ad-blocker, CSP, suppressed navigation) is diagnosable
      // from the browser console.
      console.log('[OAuth] Web signInWithOAuth returned, auth URL:', data?.url ?? '(none)');
      return { error };
    }

    // Warm up the in-app browser (Android Custom Tabs). Without this the
    // first launch can be slow or, on emulators without a warm browser
    // process, appear to do nothing at all when "Continue with Google"
    // is tapped.
    try {
      await WebBrowser.warmUpAsync();
    } catch {
      // warmUp is best-effort; continue to openAuthSessionAsync anyway
    }

    // Native app redirect URI — uses exp://... in Expo Go or spillthereel://... in standalone builds
    const redirectUrl = buildOAuthRedirectUrl();
    await setOAuthNext(next);

    console.log('[OAuth] Native redirectUrl:', redirectUrl);

    const { data, error } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: redirectUrl,
        skipBrowserRedirect: true,
      },
    });

    if (error) {
      console.error('[OAuth] Supabase signInWithOAuth error:', error);
      return { error };
    }

    if (!data?.url) {
      return {
        error: {
          name: 'OAuthError',
          message: 'No authorization URL returned from Supabase',
          status: 400,
        } as AuthError,
      };
    }

    console.log('[OAuth] Opening auth URL in WebBrowser:', data.url);

    // Open in-app WebBrowser overlay to complete Google/Apple login
    const authResult = await WebBrowser.openAuthSessionAsync(data.url, redirectUrl);

    try {
      await WebBrowser.coolDownAsync();
    } catch {
      // best-effort
    }

    console.log('[OAuth] WebBrowser result type:', authResult.type);

    if (authResult.type === 'success' && authResult.url) {
      return await handleOAuthCallbackUrl(authResult.url);
    }

    if (authResult.type === 'cancel' || authResult.type === 'dismiss') {
      return {
        error: {
          name: 'OAuthCancelled',
          message: 'Sign in was cancelled or dismissed',
          status: 400,
        } as AuthError,
      };
    }

    // 'locked' (another auth session already in flight) and any future
    // result types: surface as a real error so the screen can tell the
    // user something went wrong instead of silently staying put.
    return {
      error: {
        name: 'OAuthFailed',
        message:
          authResult.type === 'locked'
            ? 'Another sign-in is already in progress — please wait and try again'
            : `Sign-in browser closed unexpectedly (${authResult.type}). Check network and try again.`,
        status: 400,
      } as AuthError,
    };
  } catch (err: any) {
    console.error('[OAuth] Exception in performOAuthSignIn:', err);
    return {
      error: {
        name: 'OAuthException',
        message: err?.message || 'An unexpected error occurred during OAuth sign in',
        status: 500,
      } as AuthError,
    };
  }
}

/**
 * Parses and processes the callback URL from Supabase OAuth return.
 */
export async function handleOAuthCallbackUrl(
  url: string,
): Promise<{ error: AuthError | null }> {
  try {
    console.log('[OAuth] Processing callback URL:', url);
    const params = parseUrlParams(url);

    if (params.access_token && params.refresh_token) {
      const { error } = await supabase.auth.setSession({
        access_token: params.access_token,
        refresh_token: params.refresh_token,
      });
      return { error };
    } else if (params.code) {
      const { error } = await supabase.auth.exchangeCodeForSession(params.code);
      return { error };
    } else if (params.error_description) {
      return {
        error: {
          name: 'OAuthCallbackError',
          message: params.error_description,
          status: 400,
        } as AuthError,
      };
    }

    return {
      error: {
        name: 'OAuthCallbackError',
        message: 'No auth tokens or code found in redirect URL',
        status: 400,
      } as AuthError,
    };
  } catch (err: any) {
    return {
      error: {
        name: 'OAuthCallbackException',
        message: err?.message || 'Failed to process OAuth callback',
        status: 500,
      } as AuthError,
    };
  }
}

function parseUrlParams(url: string): Record<string, string> {
  const params: Record<string, string> = {};
  const fragment = url.includes('#') ? url.split('#')[1] : '';
  const query = url.includes('?') ? url.split('?')[1]?.split('#')[0] : '';
  const combined = [query, fragment].filter(Boolean).join('&');

  if (!combined) return params;

  const pairs = combined.split('&');
  for (const pair of pairs) {
    const [key, value] = pair.split('=');
    if (key && value !== undefined) {
      params[decodeURIComponent(key)] = decodeURIComponent(value);
    }
  }
  return params;
}
