/**
 * OAuth helper — handles Google and Apple sign in using Supabase Auth
 * and Expo's WebBrowser / makeRedirectUri.
 *
 * Apple takes the native path on iOS (App Store Review Guideline 4.8:
 * offering Google Sign-In means Sign In with Apple must be native, not
 * a web view). Everywhere else Apple falls back to the web-OAuth flow.
 */
import { makeRedirectUri } from 'expo-auth-session';
import * as AppleAuthentication from 'expo-apple-authentication';
import * as Crypto from 'expo-crypto';
import * as WebBrowser from 'expo-web-browser';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';
import { supabase } from '@/lib/supabase';
import type { AuthError } from '@supabase/supabase-js';

// Completes auth session for web / in-app web browser
WebBrowser.maybeCompleteAuthSession();

/** Dev-only logging — never print auth internals in release builds. */
function oauthLog(...args: unknown[]): void {
  if (__DEV__) {
    console.log(...args);
  }
}

function oauthWarn(...args: unknown[]): void {
  if (__DEV__) {
    console.warn(...args);
  }
}

function oauthError(...args: unknown[]): void {
  if (__DEV__) {
    console.error(...args);
  }
}

/**
 * Strip tokens from a URL before logging. The implicit-flow callback can
 * carry access_token/refresh_token in the fragment and the PKCE flow a
 * one-time code in the query — all of which must never land in logs.
 */
export function sanitizeUrlForLog(url: string): string {
  return url.replace(
    /((?:access_token|refresh_token|id_token|code|token)=)[^&#]*/gi,
    '$1[redacted]',
  );
}

/**
 * Where to send the user after the OAuth round-trip completes.
 * Stored before opening the browser so app/auth/callback (which runs
 * in a fresh navigation context via deep link) can route correctly.
 * Login → home, signup → import flow.
 */
const OAUTH_NEXT_KEY = 'spillthereel.oauth_next';

export type OAuthNext = 'login' | 'signup' | 'name';

export async function setOAuthNext(next: OAuthNext): Promise<void> {
  try {
    await AsyncStorage.setItem(OAUTH_NEXT_KEY, next);
  } catch {
    // non-fatal: callback falls back to home
  }
}

export async function consumeOAuthNext(): Promise<OAuthNext> {
  try {
    const value = await AsyncStorage.getItem(OAUTH_NEXT_KEY);
    await AsyncStorage.removeItem(OAUTH_NEXT_KEY);
    if (value === 'signup' || value === 'name') {
      return value;
    }
    return 'login';
  } catch {
    return 'login';
  }
}

/** Resolve a stored destination to its route. */
export function routeForOAuthNext(next: OAuthNext): string {
  if (next === 'signup') {
    return '/(import)/import1';
  }
  if (next === 'name') {
    return '/(auth)/your-name';
  }
  return '/(app)/home';
}

export function buildOAuthRedirectUrl(): string {
  // Uses exp://.../--/auth/callback under Expo Go, or spillthereel://auth/callback in standalone builds.
  // This URL MUST be whitelisted in Supabase Dashboard → Authentication → URL Configuration → Redirect URLs.
  return makeRedirectUri({
    scheme: 'spillthereel',
    path: 'auth/callback',
  });
}

export function buildPasswordResetRedirectUrl(): string {
  // Same registration rule as buildOAuthRedirectUrl: this URL MUST be
  // whitelisted in Supabase Dashboard → Authentication → URL Configuration
  // → Redirect URLs (see docs/oauth-setup.md), otherwise Supabase falls
  // back to the Site URL and the recovery link never reaches the app.
  if (Platform.OS === 'web') {
    return `${window.location.origin}/auth/reset-password`;
  }
  return makeRedirectUri({
    scheme: 'spillthereel',
    path: 'auth/reset-password',
  });
}

/**
 * Native Sign In with Apple (iOS only), per Supabase's documented
 * expo-apple-authentication pattern: SHA-256-hashed nonce to Apple,
 * raw nonce + identity token to signInWithIdToken. Falls back to the
 * web-OAuth flow when native Apple auth is unavailable.
 */
async function performNativeAppleSignIn(
  next: OAuthNext,
): Promise<{ error: AuthError | null }> {
  try {
    const available = await AppleAuthentication.isAvailableAsync();
    if (!available) {
      oauthLog('[OAuth] Native Apple auth unavailable, falling back to web flow');
      return performWebOAuthSignIn('apple', next);
    }

    const rawNonceBytes = await Crypto.getRandomBytesAsync(32);
    const rawNonce = Array.from(rawNonceBytes)
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('');
    const hashedNonce = await Crypto.digestStringAsync(
      Crypto.CryptoDigestAlgorithm.SHA256,
      rawNonce,
    );

    const credential = await AppleAuthentication.signInAsync({
      requestedScopes: [
        AppleAuthentication.AppleAuthenticationScope.FULL_NAME,
        AppleAuthentication.AppleAuthenticationScope.EMAIL,
      ],
      nonce: hashedNonce,
    });

    if (!credential.identityToken) {
      return {
        error: {
          name: 'OAuthFailed',
          message: 'Apple did not return an identity token',
          status: 400,
        } as AuthError,
      };
    }

    await setOAuthNext(next);
    const { error } = await supabase.auth.signInWithIdToken({
      provider: 'apple',
      token: credential.identityToken,
      nonce: rawNonce,
    });
    return { error };
  } catch (err: any) {
    // Never log the error object itself — it can embed tokens.
    oauthError('[OAuth] Native Apple sign-in failed');
    if (err?.code === 'ERR_REQUEST_CANCELED') {
      return {
        error: {
          name: 'OAuthCancelled',
          message: 'Sign in was cancelled',
          status: 400,
        } as AuthError,
      };
    }
    return {
      error: {
        name: 'OAuthException',
        message: err?.message || 'Apple sign-in failed',
        status: 500,
      } as AuthError,
    };
  }
}

export async function performOAuthSignIn(
  provider: 'google' | 'apple',
  next: OAuthNext = 'login',
): Promise<{ error: AuthError | null }> {
  if (provider === 'apple' && Platform.OS === 'ios') {
    return performNativeAppleSignIn(next);
  }
  if (provider === 'apple') {
    return performWebOAuthSignIn(provider, next);
  }
  return performWebOAuthSignIn(provider, next);
}

/**
 * Browser-based OAuth: full-page redirect on web, in-app WebBrowser
 * session on native. Used for Google everywhere and for Apple outside
 * iOS (or when native Apple auth is unavailable).
 */
async function performWebOAuthSignIn(
  provider: 'google' | 'apple',
  next: OAuthNext,
): Promise<{ error: AuthError | null }> {
  try {
    if (Platform.OS === 'web') {
      const redirectUrl = typeof window !== 'undefined' ? `${window.location.origin}` : undefined;
      oauthLog('[OAuth] Web redirectTo:', redirectUrl);
      await setOAuthNext(next);
      const { data, error } = await supabase.auth.signInWithOAuth({
        provider,
        options: {
          redirectTo: redirectUrl,
        },
      });
      // supabase-js performs window.location.assign(data.url) itself here —
      // this page is about to unload for the provider. Log (sanitized) so
      // a missing redirect (ad-blocker, CSP, suppressed navigation) is
      // diagnosable from the browser console.
      oauthLog(
        '[OAuth] Web signInWithOAuth returned, auth URL:',
        data?.url ? sanitizeUrlForLog(data.url) : '(none)',
      );
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

    oauthLog('[OAuth] Native redirectUrl:', redirectUrl);

    const { data, error } = await supabase.auth.signInWithOAuth({
      provider,
      options: {
        redirectTo: redirectUrl,
        skipBrowserRedirect: true,
      },
    });

    if (error) {
      // Log the message only — error objects can embed auth URLs/tokens.
      oauthError('[OAuth] Supabase signInWithOAuth error:', error.message);
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

    oauthLog('[OAuth] Opening auth URL in WebBrowser:', sanitizeUrlForLog(data.url));

    // Open in-app WebBrowser overlay to complete Google/Apple login
    const authResult = await WebBrowser.openAuthSessionAsync(data.url, redirectUrl);

    try {
      await WebBrowser.coolDownAsync();
    } catch {
      // best-effort
    }

    oauthLog('[OAuth] WebBrowser result type:', authResult.type);

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
    // Never log the error object itself — it can embed tokens/URLs.
    oauthError('[OAuth] Exception in performWebOAuthSignIn');
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
    oauthLog('[OAuth] Processing callback URL:', sanitizeUrlForLog(url));
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
