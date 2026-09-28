/**
 * Product analytics — thin wrapper over the PostHog client.
 *
 * Privacy rules (enforced here, not by convention):
 *   - Never send emails, auth-flow URLs, or tokens. Props are scrubbed.
 *   - Identify by Supabase user id ONLY, after sign-in; reset on sign-out.
 *   - No session replay, no autocapture (see PostHogProvider options in
 *     app/_layout.tsx) — every event below is an explicit track() call.
 *   - Every function is a safe no-op when PostHog is disabled (no key).
 */
import type { PostHog } from 'posthog-react-native';

/** Event schema — TBD. Extend with string literals, e.g. 'share_saved'. */
export type AnalyticsEvent = never;

/** Flat scalar props only — keeps events queryable and scrub-safe. */
type Props = Record<string, string | number | boolean | null>;

const SENSITIVE_KEY = /(email|e-mail|token|password|secret|code|authorization|url|href|link)$/i;

function scrubProps(props: Props): Props {
  const clean: Props = {};
  for (const [key, value] of Object.entries(props)) {
    if (SENSITIVE_KEY.test(key)) {
      continue;
    }
    clean[key] =
      typeof value === 'string' && value.length > 500 ? `${value.slice(0, 500)}…` : value;
  }
  return clean;
}

let client: Pick<PostHog, 'capture' | 'identify' | 'reset'> | null = null;

/** Called once by AnalyticsBootstrap inside the PostHogProvider. */
export function setAnalyticsClient(
  next: Pick<PostHog, 'capture' | 'identify' | 'reset'> | null,
): void {
  client = next;
}

export function track(event: AnalyticsEvent, props: Props = {}): void {
  try {
    client?.capture(event, scrubProps(props));
  } catch {
    // analytics must never break the app
  }
}

/** Identify by Supabase user id only — never email or metadata. */
export function identifyUser(userId: string): void {
  try {
    client?.identify(userId);
  } catch {
    // no-op
  }
}

export function resetAnalytics(): void {
  try {
    client?.reset();
  } catch {
    // no-op
  }
}
