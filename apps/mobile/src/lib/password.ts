/**
 * Password rules — single source of truth for signup and password reset.
 *
 * Rule: minimum 8 characters with at least one letter AND one number.
 * Heavier checks (breach screening) live server-side: enable Supabase's
 * built-in leaked-password protection (HaveIBeenPwned) in the dashboard
 * under Auth → Password protection, rather than shipping a local
 * blocklist with the app.
 */

export const MIN_PASSWORD_LENGTH = 8;

/**
 * Returns an error message if the password fails the rules, else null.
 * Pure client-side pre-check — runs before any network call.
 */
export function validatePassword(password: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `Password needs at least ${MIN_PASSWORD_LENGTH} characters`;
  }
  if (!/[A-Za-z]/.test(password) || !/[0-9]/.test(password)) {
    return 'Password needs at least one letter and one number';
  }
  return null;
}

/** Mismatch message, or null when both entries agree. */
export function validatePasswordMatch(password: string, confirm: string): string | null {
  if (password !== confirm) {
    return 'Passwords do not match';
  }
  return null;
}
