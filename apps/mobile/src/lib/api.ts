/**
 * API client — every backend call goes through here.
 *
 * Responsibilities:
 *   1. Prefix with API_BASE_URL.
 *   2. Attach the current Supabase JWT as `Authorization: Bearer …`.
 *   3. Parse JSON responses; surface API errors as thrown ApiErrors.
 *   4. Centralized session-expiry handling: the Supabase JS client already
 *      tries to refresh a stale access token; if the backend STILL answers
 *      401, the session is dead (revoked, deleted user, rotated keys), so
 *      sign out locally before re-throwing. useAuth's onAuthStateChange
 *      subscription drives the AuthGate to /(auth)/welcome from there —
 *      no per-screen 401 handling needed.
 *
 * All screens should call this via TanStack Query — never `fetch()` a
 * `/v1/…` URL directly. That keeps the token-attach logic in one place.
 */
import { supabase } from '@/lib/supabase';

// EXPO_PUBLIC_API_URL wins when set (preview/production EAS builds).
// Otherwise localhost in dev, and the Cloud Run URL in release builds.
const API_BASE_URL =
  process.env.EXPO_PUBLIC_API_URL ||
  (__DEV__ ? 'http://localhost:8000' : 'https://api.spillthereel.app');

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
  /** AbortSignal for cancellable requests (chat search cancel-on-switch). */
  signal?: AbortSignal;
}

async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body = null, auth = true, signal } = options;
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
    // AbortError (a DOMException, NOT an ApiError) propagates to the caller,
    // which must ignore it when it aborted the request itself.
    ...(signal ? { signal } : null),
  });

  const isJson = response.headers.get('content-type')?.includes('application/json');
  const payload = isJson ? await response.json() : await response.text();

  if (!response.ok) {
    if (auth && response.status === 401) {
      // Session is dead despite the client's refresh attempt — clear it
      // so AuthGate routes to welcome instead of retrying forever.
      // Best-effort: never mask the original API error if signOut throws.
      try {
        await supabase.auth.signOut();
      } catch {
        // no-op
      }
    }
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

/**
 * POST /v1/search — chat recall.
 *
 * Contract (backend guarantees): request {query 1..500, filters?, top_k?},
 * response {text, cards[], citations[], meta{}}. Errors 400/401/422/503/504
 * surface as ApiError with the backend's JSON detail as message; a client-side
 * abort surfaces as DOMException AbortError (not ApiError — ignore it when
 * the caller cancelled).
 */
export interface SearchFilters {
  platforms?: string[];
  categories?: string[];
  collection_ids?: string[];
  only_fully_indexed?: boolean;
  since?: string;
}

export interface SearchCard {
  item_id: string;
  title: string;
  creator?: string;
  platform: string;
  thumbnail_url?: string;
  deep_link: string;
  score: number;
}

export interface SearchCitation {
  item_id: string;
  index: number;
  span?: string;
}

export interface SearchMeta {
  latency_ms: number;
  hits: number;
  model: string;
  fallback?: boolean;
}

export interface SearchResponse {
  text: string;
  cards: SearchCard[];
  citations: SearchCitation[];
  meta: SearchMeta;
}

export interface SearchOptions {
  top_k?: number;
  signal?: AbortSignal;
}

export function search(
  query: string,
  filters?: SearchFilters,
  opts?: SearchOptions,
): Promise<SearchResponse> {
  return api.post<SearchResponse>(
    '/v1/search',
    {
      query,
      ...(filters ? { filters } : null),
      ...(typeof opts?.top_k === 'number' ? { top_k: opts.top_k } : null),
    },
    opts?.signal ? { signal: opts.signal } : undefined,
  );
}

/**
 * GET /v1/items/{id} — item detail.
 *
 * Contract (apps/api/app/api/v1/items.py `ItemDetail`): ItemSummary fields
 * (id, source_url, platform, state, saved_at, title?, summary?,
 * thumbnail_url?, owner_username?) plus transcript?, on_screen_text?,
 * caption?, hashtags[], owner_name?, owner_url?, duration_seconds?,
 * failure_reason?. No categories array — use hashtags[] for key chips.
 * Other-user or deleted ids surface as ApiError 404.
 */
export interface ItemDetail {
  id: string;
  source_url: string;
  platform: string;
  state: string;
  saved_at: string;
  title: string | null;
  summary: string | null;
  thumbnail_url: string | null;
  owner_username: string | null;
  transcript: string | null;
  on_screen_text: string | null;
  caption: string | null;
  hashtags: string[];
  owner_name: string | null;
  owner_url: string | null;
  duration_seconds: number | null;
  failure_reason: string | null;
}

export function getItem(id: string): Promise<ItemDetail> {
  return api.get<ItemDetail>(`/v1/items/${encodeURIComponent(id)}`);
}

/**
 * POST /v1/items/{id}/retry — re-queue a failed item for ingestion.
 *
 * Allowed from `failed` only (422 otherwise); already-`queued` is an
 * idempotent no-op. Returns the new state; callers should re-fetch
 * getItem() to refresh the detail view.
 */
export interface ItemActionResponse {
  item_id: string;
  state: string;
}

export function retryItem(id: string): Promise<ItemActionResponse> {
  return api.post<ItemActionResponse>(`/v1/items/${encodeURIComponent(id)}/retry`, {});
}
