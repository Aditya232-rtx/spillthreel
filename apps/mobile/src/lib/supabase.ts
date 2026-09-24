/**
 * Supabase JS client — one shared instance for the whole app.
 *
 * Session persistence: refresh + access tokens live in the OS keychain
 * (iOS) / Android Keystore via `expo-secure-store`. This matches TRD §5.2
 * ("refresh token persisted in expo-secure-store") and means signing out
 * from settings actually clears the tokens from disk, not just memory.
 *
 * Do NOT import this directly from React components — go through
 * `useAuth()` in `src/hooks/useAuth.ts`. That gives us one place to
 * mock the client in tests and one place to react to auth-state
 * changes.
 */
import 'react-native-url-polyfill/auto';
import 'expo-crypto';
import { createClient } from '@supabase/supabase-js';
import * as SecureStore from 'expo-secure-store';
import { AppState, Platform } from 'react-native';

// Environment variables from EXPO_PUBLIC_* — no fallbacks. A missing env
// var fails loudly at boot instead of silently pointing the app at a
// real project's values baked into source (see .env.example).
const SUPABASE_URL = process.env.EXPO_PUBLIC_SUPABASE_URL;
const SUPABASE_PUBLISHABLE_KEY = process.env.EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY;

if (!SUPABASE_URL || !SUPABASE_PUBLISHABLE_KEY) {
  throw new Error(
    'Missing EXPO_PUBLIC_SUPABASE_URL or EXPO_PUBLIC_SUPABASE_PUBLISHABLE_KEY. ' +
      'Copy apps/mobile/.env.example to apps/mobile/.env and fill in your ' +
      'Supabase project values (Dashboard → Project Settings → API).',
  );
}

/**
 * SecureStore-backed storage adapter for Supabase's auth session on native platforms,
 * falling back to localStorage on web.
 */
const ExpoSecureStoreAdapter = {
  async getItem(key: string): Promise<string | null> {
    if (Platform.OS === 'web') {
      try {
        return typeof localStorage !== 'undefined' ? localStorage.getItem(key) : null;
      } catch {
        return null;
      }
    }
    return SecureStore.getItemAsync(key);
  },
  async setItem(key: string, value: string): Promise<void> {
    if (Platform.OS === 'web') {
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.setItem(key, value);
        }
      } catch {}
      return;
    }
    await SecureStore.setItemAsync(key, value);
  },
  async removeItem(key: string): Promise<void> {
    if (Platform.OS === 'web') {
      try {
        if (typeof localStorage !== 'undefined') {
          localStorage.removeItem(key);
        }
      } catch {}
      return;
    }
    await SecureStore.deleteItemAsync(key);
  },
};

export const supabase = createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY, {
  auth: {
    storage: ExpoSecureStoreAdapter,
    autoRefreshToken: true,
    persistSession: true,
    // Web: Supabase redirects back to window.location.origin with the
    // session in the URL fragment/query — the client MUST parse it.
    // Native: there is no URL to detect (we handle the return manually
    // via WebBrowser.openAuthSessionAsync + app/auth/callback), so keep
    // it off to avoid spurious parses.
    detectSessionInUrl: Platform.OS === 'web',
    flowType: 'pkce',
  },
});

/**
 * Supabase's autoRefreshToken timer runs a `setInterval` that won't fire
 * when the RN app is backgrounded. This wires it to the foreground/
 * background lifecycle so the access token is refreshed as soon as the
 * user comes back — closing the "token expired while I was away" gap
 * that would otherwise cause the first post-resume request to 401.
 *
 * Per Supabase docs — https://supabase.com/docs/reference/javascript/auth-startautorefresh
 */
AppState.addEventListener('change', (state) => {
  if (state === 'active') {
    supabase.auth.startAutoRefresh();
  } else {
    supabase.auth.stopAutoRefresh();
  }
});
