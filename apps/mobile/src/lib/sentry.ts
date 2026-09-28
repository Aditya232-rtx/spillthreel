/**
 * Sentry init — call once from the root layout.
 *
 * Silent no-op when EXPO_PUBLIC_SENTRY_DSN is unset, so local dev never
 * breaks. Privacy-first: OAuth callback URLs can carry access_token /
 * refresh_token / code in query or fragment, so every URL, message and
 * breadcrumb is scrubbed before leaving the device.
 */
import * as Sentry from '@sentry/react-native';
import Constants from 'expo-constants';

const SENSITIVE_PATTERN = /((?:access_token|refresh_token|id_token|code|token|authorization)=)[^&#\s]*/gi;

/** Redact token-shaped values; drop URL fragments entirely (token carriers). */
export function scrubUrl(value: string): string {
  const withoutFragment = value.split('#')[0];
  return withoutFragment.replace(SENSITIVE_PATTERN, '$1[redacted]');
}

function scrubText(value: unknown): unknown {
  if (typeof value !== 'string') {
    return value;
  }
  return value.includes('spillthereel://') || value.includes('http')
    ? scrubUrl(value)
    : value.replace(SENSITIVE_PATTERN, '$1[redacted]');
}

function scrubEvent(event: Sentry.ErrorEvent): Sentry.ErrorEvent {
  if (event.request?.url) {
    event.request.url = scrubUrl(event.request.url);
  }
  if (event.request?.headers) {
    const headers = event.request.headers as Record<string, unknown>;
    for (const key of Object.keys(headers)) {
      if (/^(authorization|cookie|set-cookie)$/i.test(key)) {
        headers[key] = '[redacted]';
      }
    }
  }
  for (const value of event.exception?.values ?? []) {
    if (value.value) {
      value.value = scrubText(value.value) as string;
    }
  }
  return event;
}

function scrubBreadcrumb(crumb: Sentry.Breadcrumb): Sentry.Breadcrumb | null {
  if (crumb.data?.url) {
    crumb.data.url = scrubUrl(String(crumb.data.url));
  }
  if (typeof crumb.message === 'string') {
    crumb.message = scrubText(crumb.message) as string;
  }
  return crumb;
}

export function initSentry(): void {
  const dsn = process.env.EXPO_PUBLIC_SENTRY_DSN;
  if (!dsn) {
    return;
  }
  const sampleRate = Number(process.env.EXPO_PUBLIC_SENTRY_TRACES_SAMPLE_RATE ?? '0') || 0;
  const version = Constants.expoConfig?.version ?? 'dev';
  Sentry.init({
    dsn,
    sendDefaultPii: false,
    tracesSampleRate: sampleRate,
    environment: __DEV__ ? 'development' : 'production',
    release: `spillthereel@${version}`,
    beforeSend: (event) => scrubEvent(event),
    beforeBreadcrumb: (crumb) => scrubBreadcrumb(crumb),
  });
}
