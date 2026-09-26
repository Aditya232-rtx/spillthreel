/**
 * Display-name helpers — one place that decides what to call the user.
 *
 * Source of truth is the Supabase user_metadata.full_name, set at email
 * signup, prefilled from Google/Apple, or entered on the your-name
 * screen. Falls back to the email prefix, then a generic greeting word.
 *
 * Last-explicit-choice wins: whenever the user types a name anywhere
 * (signup, your-name, profile edit), we remember it per email. A later
 * OAuth login whose provider ships a different name would otherwise
 * silently overwrite it — useAuth re-applies the remembered choice on
 * every SIGNED_IN event.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import type { User } from '@supabase/supabase-js';

const OVERRIDE_PREFIX = 'spillthereel.display_name.';

export async function rememberDisplayName(email: string, name: string): Promise<void> {
  try {
    await AsyncStorage.setItem(OVERRIDE_PREFIX + email.toLowerCase(), name);
  } catch {
    // non-fatal: worst case a provider name wins until next edit
  }
}

export async function getRememberedDisplayName(email: string): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(OVERRIDE_PREFIX + email.toLowerCase());
  } catch {
    return null;
  }
}

export function getFullName(user: User | null): string | null {
  const raw = user?.user_metadata?.full_name;
  if (typeof raw === 'string' && raw.trim()) {
    return raw.trim();
  }
  return null;
}

/** First name for greetings ("Aditya Jadhav" → "Aditya"). */
export function getFirstName(user: User | null): string {
  const full = getFullName(user);
  if (full) {
    return full.split(/\s+/)[0];
  }
  const email = user?.email;
  if (email && email.includes('@')) {
    const prefix = email.split('@')[0].replace(/[._-]+/g, ' ').trim();
    if (prefix) {
      return prefix.split(/\s+/)[0];
    }
  }
  return 'friend';
}
