/**
 * API client — every backend call goes through here.
 *
 * Responsibilities:
 *   1. Prefix with API_BASE_URL.
 *   2. Attach the current Supabase JWT as `Authorization: Bearer …`.
 *   3. Parse JSON responses; surface API errors as thrown ApiErrors.
 *   4. Handle the "session expired" case by clearing local state (the
 *      Supabase JS client already tries to refresh; if we still get 401
 *      after refresh, we're truly signed out).
 *
 * All screens should call this via TanStack Query — never `fetch()` a
 * `/v1/…` URL directly. That keeps the token-attach logic in one place.
 */
import { supabase } from '@/lib/supabase';

// In dev, this points at localhost:8000 (docker-compose api). In prod
// it's the Cloud Run URL. Swap via env-time constant later — for now
// __DEV__ is enough.
const API_BASE_URL = __DEV__
  ? 'http://localhost:8000'
  : 'https://api.spillthereel.app';

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly body: unknown,
    message: string,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'DELETE';
  body?: Record<string, unknown> | null;
  /** Include auth header? Default true. Set false for public routes. */
  auth?: boolean;
}

async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body = null, auth = true } = options;
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    Accept: 'application/json',
  };

  if (auth) {
    // getSession() returns the cached session synchronously if valid, or
    // triggers a refresh if the access token is close to expiry.
    const { data } = await supabase.auth.getSession();
    const token = data.session?.access_token;
    if (!token) {
      throw new ApiError(401, null, 'no_active_session');
    }
    headers.Authorization = `Bearer ${token}`;
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });

  const isJson = response.headers.get('content-type')?.includes('application/json');
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    throw new ApiError(
      response.status,
      payload,
      typeof payload === 'object' && payload !== null && 'detail' in payload
        ? String((payload as { detail: unknown }).detail)
        : `HTTP ${response.status}`,
    );
  }

  return payload as T;
}

export const api = {
  get: <T,>(path: string, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'GET' }),

  post: <T,>(path: string, body: Record<string, unknown>, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'POST', body }),

  patch: <T,>(path: string, body: Record<string, unknown>, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'PATCH', body }),

  delete: <T,>(path: string, opts?: Omit<RequestOptions, 'method' | 'body'>) =>
    apiRequest<T>(path, { ...opts, method: 'DELETE' }),
};
