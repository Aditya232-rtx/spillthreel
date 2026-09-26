/**
 * Email-verification helpers.
 *
 * Supabase only issues a session after the confirmation link is tapped,
 * so a pending signup has no session: the user can browse the (static)
 * import guide, but home/API stay gated. These helpers bridge that gap:
 * remember the pending address, offer resends, and nudge until confirmed.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { supabase } from '@/lib/supabase';

const PENDING_EMAIL_KEY = 'spillthereel.pending_confirm_email';
const DISMISSED_PREFIX = 'spillthereel.verify_dismissed.';

export async function setPendingEmail(email: string): Promise<void> {
  try {
    await AsyncStorage.setItem(PENDING_EMAIL_KEY, email);
  } catch {
    // non-fatal: nudges just won't survive a restart
  }
}

export async function getPendingEmail(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(PENDING_EMAIL_KEY);
  } catch {
    return null;
  }
}

export async function clearPendingEmail(): Promise<void> {
  try {
    await AsyncStorage.removeItem(PENDING_EMAIL_KEY);
  } catch {
    // no-op
  }
}

/** Resend the signup confirmation mail. Returns an error message or null. */
export async function resendConfirmation(email: string): Promise<string | null> {
  const { error } = await supabase.auth.resend({ type: 'signup', email });
  return error ? error.message : null;
}

export async function isVerifyDismissed(userId: string): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(DISMISSED_PREFIX + userId)) === '1';
  } catch {
    return false;
  }
}

export async function dismissVerifyNotice(userId: string): Promise<void> {
  try {
    await AsyncStorage.setItem(DISMISSED_PREFIX + userId, '1');
  } catch {
    // no-op
  }
}
